"""Moderation cards in Telegram admin chats."""

from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from bot.adapters.telegram import keyboards
from bot.adapters.telegram import media as media_utils
from bot.adapters.telegram.publisher import file_input, input_media
from bot.core import (
    AdminService,
    ContentType,
    ModerationRef,
    ModerationService,
    Platform,
    Source,
    Submission,
    SubmissionStatus,
)

logger = logging.getLogger(__name__)

TG_CAPTION_LIMIT = 1024

SOURCE_TITLES: dict[Source, str] = {
    Source.telegram: "Telegram",
    Source.discord: "Discord",
}

MEDIA_TITLES: dict[ContentType, str] = {
    ContentType.photo: "фото",
    ContentType.video: "видео",
    ContentType.sticker: "стикер",
    ContentType.link: "ссылка",
}

STATUS_LINES: dict[SubmissionStatus, str] = {
    SubmissionStatus.draft: "📝 Черновик",
    SubmissionStatus.awaiting_privacy: "📝 Черновик",
    SubmissionStatus.pending: "⏳ На модерации",
    SubmissionStatus.approved: "✅ Одобрено",
    SubmissionStatus.scheduled: "🕓 Отложено",
    SubmissionStatus.published: "✅ Опубликовано",
    SubmissionStatus.rejected: "❌ Отклонено",
}


def _fmt_time(submission: Submission) -> str:
    value = submission.scheduled_at
    if value is None:
        return ""
    return value.astimezone().strftime("%d.%m.%Y %H:%M")


def status_line_for(submission: Submission) -> str:
    base = STATUS_LINES.get(submission.status, submission.status.value)
    if submission.status is SubmissionStatus.published:
        mode = "с именем" if submission.want_anonymous is False else "анонимно"
        return f"{base} ({mode})"
    if submission.status is SubmissionStatus.scheduled:
        moment = _fmt_time(submission)
        return f"{base} до {moment}" if moment else base
    if submission.status is SubmissionStatus.rejected and submission.reject_reason:
        return f"{base} (с причиной)"
    return base


def media_summary(submission: Submission) -> str:
    counts: dict[ContentType, int] = {}
    for item in submission.media:
        counts[item.content_type] = counts.get(item.content_type, 0) + 1
    if not counts:
        return ""
    parts = [
        f"{MEDIA_TITLES.get(kind, kind.value)} × {count}"
        for kind, count in counts.items()
    ]
    return "📎 " + ", ".join(parts)


def format_card(submission: Submission, *, status_line: str | None = None) -> str:
    """HTML body of a moderation card; all author content is escaped."""
    lines = [f"📩 <b>Заявка #{submission.id}</b>", ""]
    lines.append(f"👤 {escape(submission.author_display_name)}")
    if submission.author_username:
        lines.append(f"@{escape(submission.author_username)}")
    lines.append(f"ID: <code>{escape(submission.author_platform_user_id)}</code>")
    lines.append(f"🗨 Источник: {SOURCE_TITLES.get(submission.source, '—')}")
    target_labels = {
        "telegram": "только Telegram",
        "discord": "только Discord",
        "both": "Telegram и Discord",
    }
    lines.append(
        "📣 Куда: "
        + target_labels.get(
            submission.publish_target.value, submission.publish_target.value
        )
    )
    if submission.is_admin_post:
        lines.append("🛠 Пост администратора (без #предложка)")

    if submission.want_anonymous is True:
        lines.append("🕶 <b>Автор просит анонимность</b>")
    elif submission.want_anonymous is False:
        lines.append("👤 <b>Автор согласен на публикацию с именем</b>")

    text = (submission.text or "").strip()
    if text:
        lines.extend(["", escape(text)])
    else:
        lines.extend(["", "<i>Без текста</i>"])

    summary = media_summary(submission)
    if summary:
        lines.extend(["", summary])

    if submission.reject_reason:
        lines.extend(["", f"✍️ Причина: {escape(submission.reject_reason)}"])

    lines.extend(["", f"<b>{status_line or status_line_for(submission)}</b>"])
    return "\n".join(lines)


def card_carries_media(submission: Submission) -> bool:
    """True when the card itself is a photo/video message (caption edits)."""
    return media_utils.split_media(submission).is_single_visual


class TelegramCards:
    """Sends and refreshes moderation cards for every Telegram admin."""

    def __init__(
        self,
        bot: Bot,
        *,
        moderation: ModerationService,
        admins: AdminService,
    ) -> None:
        self.bot = bot
        self.moderation = moderation
        self.admins = admins

    def admin_chat_ids(self) -> list[int]:
        chat_ids: list[int] = []
        for admin in self.admins.list_admins(platform=Platform.telegram):
            try:
                chat_ids.append(int(admin.platform_user_id))
            except (TypeError, ValueError):
                logger.warning(
                    "Пропущен админ с некорректным id: %r", admin.platform_user_id
                )
        return chat_ids

    async def send_cards(
        self,
        submission: Submission,
        *,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> list[ModerationRef]:
        if submission.id is None:
            return []
        markup = keyboard or keyboards.moderation_keyboard(submission.id)
        body = format_card(submission)
        refs: list[ModerationRef] = []

        for chat_id in self.admin_chat_ids():
            try:
                message_id = await self._send_card(chat_id, submission, body, markup)
            except TelegramAPIError:
                logger.exception("Не удалось отправить карточку админу %s", chat_id)
                continue
            if message_id is None:
                continue
            refs.append(
                self.moderation.save_moderation_ref(
                    submission.id,
                    platform=Platform.telegram,
                    target_id=str(chat_id),
                    message_id=str(message_id),
                )
            )
        return refs

    async def _send_card(
        self,
        chat_id: int,
        submission: Submission,
        body: str,
        markup: InlineKeyboardMarkup,
    ) -> int | None:
        split = media_utils.split_media(submission)

        if split.is_single_visual:
            item = split.visual[0]
            sender = (
                self.bot.send_photo
                if item.content_type is ContentType.photo
                else self.bot.send_video
            )
            try:
                payload = file_input(item)
            except ValueError:
                logger.warning(
                    "Нет файла для превью заявки %s, шлю текстовую карточку",
                    submission.id,
                )
                payload = None
            if payload is not None and len(body) <= TG_CAPTION_LIMIT:
                try:
                    message = await sender(
                        chat_id,
                        payload,
                        caption=body,
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
                    return message.message_id
                except TelegramAPIError:
                    logger.exception(
                        "Карточка с подписью не ушла, шлю фото и текст отдельно"
                    )
            if payload is not None:
                try:
                    await sender(chat_id, payload)
                except TelegramAPIError:
                    logger.exception(
                        "Превью фото заявки %s не ушло админу %s",
                        submission.id,
                        chat_id,
                    )
            message = await self.bot.send_message(
                chat_id, body, parse_mode="HTML", reply_markup=markup
            )
            return message.message_id

        # Albums and stickers get a preview first, then a text card with buttons.
        for chunk in media_utils.chunk_media(split.visual):
            group = []
            for item in chunk:
                try:
                    group.append(input_media(item))
                except ValueError:
                    logger.warning(
                        "Пропуск медиа без файла в заявке %s", submission.id
                    )
            if group:
                await self.bot.send_media_group(chat_id, media=group)
        for item in split.stickers:
            try:
                await self.bot.send_sticker(chat_id, file_input(item))
            except (ValueError, TelegramAPIError):
                logger.exception(
                    "Стикер заявки %s не ушёл админу %s", submission.id, chat_id
                )

        message = await self.bot.send_message(
            chat_id, body, parse_mode="HTML", reply_markup=markup
        )
        return message.message_id

    async def update_cards(
        self,
        submission: Submission,
        *,
        status_line: str | None = None,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Refresh every stored card; a card removed by an admin is skipped."""
        if submission.id is None:
            return
        body = format_card(submission, status_line=status_line)
        use_caption = card_carries_media(submission)

        for ref in self.moderation.get_moderation_refs(
            submission.id, platform=Platform.telegram
        ):
            try:
                chat_id = int(ref.target_id)
                message_id = int(ref.message_id)
            except (TypeError, ValueError):
                continue
            try:
                if use_caption:
                    await self.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=body,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                else:
                    await self.bot.edit_message_text(
                        body,
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
            except TelegramAPIError:
                try:
                    # Stored id may be the text card even when media exists.
                    await self.bot.edit_message_text(
                        body,
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                except TelegramAPIError:
                    logger.debug(
                        "Карточка %s/%s не обновлена",
                        ref.target_id,
                        ref.message_id,
                    )

    async def handle_submitted(self, event: object) -> None:
        """EventBus hook: a new submission needs cards in every admin chat."""
        submission = getattr(event, "submission", None)
        if submission is not None:
            await self.send_cards(submission)

    async def handle_decision(self, event: object) -> None:
        """EventBus hook: a decision happened anywhere — repaint the cards."""
        submission = getattr(event, "submission", None)
        if submission is not None:
            await self.update_cards(submission)
