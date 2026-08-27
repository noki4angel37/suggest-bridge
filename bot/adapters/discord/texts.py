"""Russian user-facing strings and status emoji for the Discord adapter.

The source of a submission is always named directly («Источник: Discord»).
"""

from __future__ import annotations

from collections.abc import Sequence

from bot.core.models import (
    ContentType,
    MediaItem,
    Source,
    Submission,
    SubmissionStatus,
)
from bot.core.rules import TEXT_LIMIT
from bot.settings import anon_name as _anon_name
from bot.settings import (
    setup_category_name,
    setup_editor_role_name,
    setup_mod_channel_name,
    setup_publish_channel_aliases,
    setup_publish_channel_name,
    setup_suggest_channel_name,
)

STATUS_EMOJI: dict[SubmissionStatus, str] = {
    SubmissionStatus.draft: "📝",
    SubmissionStatus.awaiting_privacy: "📝",
    SubmissionStatus.pending: "⏳",
    SubmissionStatus.approved: "✅",
    SubmissionStatus.scheduled: "🕒",
    SubmissionStatus.published: "📢",
    SubmissionStatus.rejected: "❌",
}

STATUS_LABELS: dict[SubmissionStatus, str] = {
    SubmissionStatus.draft: "черновик",
    SubmissionStatus.awaiting_privacy: "выбор подписи",
    SubmissionStatus.pending: "на модерации",
    SubmissionStatus.approved: "одобрена",
    SubmissionStatus.scheduled: "запланирована",
    SubmissionStatus.published: "опубликована",
    SubmissionStatus.rejected: "отклонена",
}

CONTENT_LABELS: dict[ContentType, str] = {
    ContentType.text: "текст",
    ContentType.photo: "фото",
    ContentType.video: "видео",
    ContentType.sticker: "стикер",
    ContentType.link: "файл или ссылка",
    ContentType.album: "альбом",
    ContentType.mixed: "разные вложения",
}

SOURCE_LABELS: dict[Source, str] = {
    Source.telegram: "Telegram",
    Source.discord: "Discord",
}

# --- buttons -----------------------------------------------------------------

BTN_ANONYMOUS = "Анонимно"
BTN_NAMED = "С именем"
BTN_SUBMIT = "Отправить"
BTN_EDIT = "Изменить текст"
BTN_CANCEL_DRAFT = "Отменить"
BTN_APPROVE = "Одобрить"
BTN_REJECT = "Отклонить"
BTN_REPLY = "Ответить автору"
BTN_SCHEDULE = "Отложить"
BTN_EDIT_MOD = "Править текст"

# --- draft -------------------------------------------------------------------

DRAFT_TITLE = "Черновик заявки"
DRAFT_HINT = (
    "Выбери подпись и нажми «Отправить». "
    "Текст можно поправить кнопкой «Изменить текст»."
)
DRAFT_DM_NOTE = (
    "Твоё сообщение в канале удалено — заявку видишь только ты "
    "(и модераторы после отправки)."
)
DRAFT_DM_CLOSED = (
    "Не удалось написать тебе в ЛС. Открой личные сообщения от участников "
    "сервера или используй команду /suggest — она видна только тебе."
)
CHANNEL_HINT_DELETED = "Сообщение скрыто. {reason}"
CHANNEL_QUEUED_HINT = "Заявка принята и ушла на модерацию."
EDIT_MODAL_TITLE = "Текст заявки"
EDIT_MODAL_LABEL = f"Текст (до {TEXT_LIMIT} символов)"
NEED_PRIVACY_CHOICE = "Сначала выбери: «Анонимно» или «С именем»."
DRAFT_NOT_YOURS = "Это чужой черновик."
DRAFT_EXPIRED = "Черновик устарел, отправь заявку заново."
DRAFT_EMPTY = "Нечего отправлять: нет ни текста, ни вложений."
DRAFT_CANCELLED = "Черновик отменён."
GUILD_ONLY = "Команда работает только на сервере."
NO_PROPOSE_ROLE = "У тебя нет роли для отправки заявок на этом сервере."
NOT_MODERATOR = "Модерация доступна только модераторам предложки."
BLOCKED = "Отправка заявок для тебя закрыта."
ALREADY_HANDLED = "Заявка уже обработана."
NOT_FOUND = "Заявка не найдена."
EDIT_SAVED = "Текст заявки обновлён."
EDIT_FORBIDDEN = "Текст можно править только до публикации."
SUBMIT_FAILED = "Не удалось отправить заявку, попробуй ещё раз."
PUBLISH_FAILED = (
    "Заявка одобрена, но опубликовать не получилось — попробуй позже."
)

ANON_CHOICE_LABELS = {
    True: f"анонимно ({_anon_name()})",
    False: "с именем автора",
}


def text_too_long(length: int) -> str:
    return (
        f"Текст длиннее {TEXT_LIMIT} символов: {length}. "
        "Сократи и попробуй снова."
    )


def antiflood(limit: int, window_sec: int) -> str:
    return (
        f"Слишком часто: не больше {limit} заявок за {window_sec} с. "
        "Подожди немного."
    )


def queued(submission_id: int) -> str:
    return f"Заявка №{submission_id} в очереди на модерацию."


def draft_created(submission_id: int) -> str:
    return f"Черновик заявки №{submission_id} готов."


def privacy_chosen(want_anonymous: bool) -> str:
    return f"Подпись: {ANON_CHOICE_LABELS[want_anonymous]}."


def source_note(submission: Submission) -> str:
    label = SOURCE_LABELS.get(submission.source, submission.source.value)
    return f"Источник: {label}"


def status_line(submission: Submission) -> str:
    emoji = STATUS_EMOJI.get(submission.status, "•")
    label = STATUS_LABELS.get(submission.status, submission.status.value)
    return f"{emoji} Статус: {label}"


def privacy_line(submission: Submission) -> str:
    if submission.want_anonymous is None:
        return "🙈 Подпись: не выбрана"
    if submission.want_anonymous:
        return f"🙈 Подпись: анонимно ({_anon_name()})"
    return "👁 Подпись: с именем автора"


def describe_media(media: Sequence[MediaItem]) -> str:
    if not media:
        return "нет"
    counts: dict[ContentType, int] = {}
    for item in media:
        counts[item.content_type] = counts.get(item.content_type, 0) + 1
    return ", ".join(
        f"{CONTENT_LABELS.get(kind, kind.value)} ×{count}"
        if count > 1
        else CONTENT_LABELS.get(kind, kind.value)
        for kind, count in counts.items()
    )


# --- moderation card ---------------------------------------------------------


def card_title(submission: Submission) -> str:
    from bot.core.rules import display_sid

    emoji = STATUS_EMOJI.get(submission.status, "•")
    return f"{emoji} Заявка {display_sid(submission.id)}"


def author_block(submission: Submission) -> str:
    """Moderators always see the real author, even for anonymous requests."""
    lines = [
        f"👤 {submission.author_display_name or _anon_name()}",
        f"🆔 {submission.author_platform_user_id}",
    ]
    if submission.author_username:
        lines.append(f"@ {submission.author_username}")
    if submission.author_discord_profile_url:
        lines.append(f"🔗 {submission.author_discord_profile_url}")
    return "\n".join(lines)


def moderator_note(display_name: str, action: str) -> str:
    return f"{action}: {display_name}"


def approved_note(submission_id: int) -> str:
    return f"Заявка №{submission_id} одобрена."


def rejected_note(submission_id: int, reason: str | None) -> str:
    if reason:
        return f"Заявка №{submission_id} отклонена. Причина: {reason}"
    return f"Заявка №{submission_id} отклонена."


def scheduled_note(submission_id: int, when: str) -> str:
    return f"Заявка №{submission_id} отложена до {when} (UTC)."


REJECT_MODAL_TITLE = "Причина отклонения"
REJECT_MODAL_LABEL = "Причина (увидит автор)"
REPLY_MODAL_TITLE = "Ответ автору"
REPLY_MODAL_LABEL = "Текст ответа"
SCHEDULE_MODAL_TITLE = "Отложить публикацию"
SCHEDULE_MODAL_LABEL = "Дата и время UTC: ГГГГ-ММ-ДД ЧЧ:ММ"
SCHEDULE_BAD_FORMAT = (
    "Не понял дату. Формат: ГГГГ-ММ-ДД ЧЧ:ММ, например 2026-08-12 19:30."
)
SCHEDULE_IN_PAST = "Это время уже прошло, укажи будущее."
MOD_CHANNEL_MISSING = (
    "Канал модерации не настроен. Запусти /setup_suggest на сервере."
)
REPLY_SENT = "Ответ отправлен автору."
REPLY_FAILED = "Не удалось написать автору: у него закрыты личные сообщения."
REPLY_CROSS_PLATFORM = (
    "Автор заявки в Telegram — ответ из Discord пока не доставляется."
)

# --- author notifications ----------------------------------------------------


def notify_approved(submission_id: int) -> str:
    return (
        f"✅ Заявка №{submission_id} одобрена и ждёт публикации в канале."
    )


def notify_scheduled(submission_id: int, when: str) -> str:
    return f"🕒 Заявка №{submission_id} запланирована на {when} (UTC)."


def notify_published(submission_id: int) -> str:
    return f"📢 Заявка №{submission_id} опубликована в канале."


def notify_rejected(submission_id: int, reason: str | None) -> str:
    if reason:
        return f"❌ Заявка №{submission_id} отклонена. Причина: {reason}"
    return f"❌ Заявка №{submission_id} отклонена."


def moderator_reply(text: str) -> str:
    return f"✉️ Ответ модератора по твоей заявке:\n\n{text}"


# --- guild setup (names from env via bot.settings) -----------------------------

SETUP_NO_RIGHTS = "Настройка доступна администраторам сервера."
PASS_SETUP_NO_RIGHTS = (
    "Нужны права админа бота, админ-роли сервера (/admin_roles) "
    "или быть владельцем сервера."
)
SETUP_FORBIDDEN = (
    "Не хватает прав: боту нужно «Управление каналами» "
    "и доступ к каналам предложки."
)
SETUP_INTRO = (
    "Пиши сюда предложение (текст до 400 символов, фото, видео, стикеры, "
    "ссылки, файлы) — сообщение сразу скроется, заявка уйдёт на модерацию "
    "(анонимно). Либо /suggest — черновик виден только тебе."
)
SETUP_MOD_INTRO = (
    "Сюда приходят карточки заявок с кнопками модерации. "
    "Доступ — только для модераторов предложки."
)
SETUP_PUBLISH_INTRO = (
    "Лента опубликованных предложек и зеркало Telegram-канала. "
    "Писать могут бот и @недоадмин. Посты синхронизируются в обе стороны."
)


def telegram_channel_public_url(channel_id: int | str | None = None) -> str | None:
    """Build https://t.me/c/<id> from CHANNEL_ID (-100…)."""
    import os

    raw = str(channel_id if channel_id is not None else os.environ.get("CHANNEL_ID", "")).strip()
    if not raw or raw == "REPLACE_ME":
        return None
    if raw.startswith("-100"):
        raw = raw[4:]
    elif raw.startswith("-"):
        raw = raw.lstrip("-")
    if not raw.isdigit():
        return None
    return f"https://t.me/c/{raw}"


def setup_publish_intro(*, channel_id: int | str | None = None) -> str:
    """Publish-channel intro; appends TG channel link once (not per mirrored post)."""
    url = telegram_channel_public_url(channel_id)
    if not url:
        return SETUP_PUBLISH_INTRO
    return f"{SETUP_PUBLISH_INTRO}\n{url}"


def decorate_done(
    *,
    renamed: int,
    moved: int,
    locked: int,
    publish_mention: str,
    errors: list[str],
) -> str:
    lines = [
        "Оформление сервера применено.",
        f"✏️ Переименовано: {renamed}",
        f"📂 Перемещено: {moved}",
        f"🔒 Закрыто на запись: {locked}",
        f"📢 Лента (approve + TG): {publish_mention}",
    ]
    if errors:
        lines.append("⚠ Ошибки:")
        lines.extend(f"• {err}" for err in errors[:8])
    return "\n".join(lines)


def setup_done(
    suggest_mention: str,
    mod_mention: str,
    publish_mention: str,
    propose_roles: str,
    mod_roles: str,
) -> str:
    return "\n".join(
        [
            "Предложка настроена.",
            f"📥 Канал заявок: {suggest_mention}",
            f"🛡 Канал модерации: {mod_mention}",
            f"📢 Канал публикации: {publish_mention}",
            f"✍️ Роли для заявок: {propose_roles}",
            f"⚖️ Роли модерации: {mod_roles}",
        ]
    )


def setup_info(
    suggest_mention: str,
    mod_mention: str,
    publish_mention: str,
    propose_roles: str,
    mod_roles: str,
    rate_limit: str,
) -> str:
    return "\n".join(
        [
            "Настройки предложки:",
            f"📥 Канал заявок: {suggest_mention}",
            f"🛡 Канал модерации: {mod_mention}",
            f"📢 Канал публикации: {publish_mention}",
            f"✍️ Роли для заявок: {propose_roles}",
            f"⚖️ Роли модерации: {mod_roles}",
            f"⏱ Лимит заявок: {rate_limit}",
        ]
    )


ROLES_EVERYONE = "все участники"
NOT_CONFIGURED = "не настроен"

# publish target override
BTN_TARGET_TG = "📣 Только TG"
BTN_TARGET_DS = "💬 Только DS"
BTN_TARGET_BOTH = "🔗 TG+DS"
PUBLISH_TARGET_LABELS = {
    "telegram": "только Telegram",
    "discord": "только Discord",
    "both": "Telegram и Discord",
}


def __getattr__(name: str) -> object:
    if name == "SETUP_CATEGORY_NAME":
        return setup_category_name()
    if name == "SETUP_SUGGEST_CHANNEL_NAME":
        return setup_suggest_channel_name()
    if name == "SETUP_MOD_CHANNEL_NAME":
        return setup_mod_channel_name()
    if name == "SETUP_PUBLISH_CHANNEL_NAME":
        return setup_publish_channel_name()
    if name == "SETUP_PUBLISH_CHANNEL_ALIASES":
        return setup_publish_channel_aliases()
    if name == "SETUP_EDITOR_ROLE_NAME":
        return setup_editor_role_name()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
