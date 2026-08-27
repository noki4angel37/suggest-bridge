"""Publish approved submissions into a Discord publish channel."""

from __future__ import annotations

import logging
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import discord
from aiogram import Bot as TelegramBot

from bot.core.models import ContentType, GuildConfig, RefKind, Submission
from bot.core.publisher import (
    PublishMedia,
    PublishMode,
    PublishPlan,
    build_publish_plan,
)
from bot.core.services import GuildConfigService

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 120
EMOJI_MARK = "📢"


@dataclass(frozen=True)
class DiscordPublishResult:
    target_id: str
    message_ids: tuple[str, ...] = ()

    @property
    def message_id(self) -> str | None:
        return self.message_ids[0] if self.message_ids else None


class DiscordChannelPublishError(RuntimeError):
    pass


class DiscordChannelPublisher:
    """Posts a PublishPlan into guild publish_channel_id."""

    def __init__(
        self,
        guilds: GuildConfigService,
        *,
        telegram_bot: TelegramBot | None = None,
        emoji_mark: str = EMOJI_MARK,
    ) -> None:
        self.guilds = guilds
        self.telegram_bot = telegram_bot
        self.emoji_mark = emoji_mark
        self._client: discord.Client | None = None

    def bind_client(self, client: discord.Client) -> None:
        self._client = client

    async def __call__(self, submission: Submission) -> DiscordPublishResult:
        return await self.publish(submission)

    async def publish(
        self,
        submission: Submission,
        *,
        with_author: bool | None = None,
    ) -> DiscordPublishResult:
        plan = build_publish_plan(submission, with_author=with_author)
        return await self.publish_plan(plan, guild_id=submission.guild_id)

    async def publish_plan(
        self,
        plan: PublishPlan,
        *,
        guild_id: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> DiscordPublishResult:
        target = channel or self._resolve_channel(guild_id)
        if target is None:
            raise DiscordChannelPublishError(
                "Не настроен канал публикации Discord (#предложка)"
            )

        body = self._with_emoji(plan.caption)
        async with AsyncExitStack() as stack:
            files = await self._resolve_files(plan, stack)
            if plan.mode is PublishMode.text or not files:
                message = await target.send(content=body or None)
            elif len(files) == 1:
                message = await target.send(
                    content=body or None, file=files[0]
                )
            else:
                message = await target.send(
                    content=body or None, files=files[:10]
                )

        result = DiscordPublishResult(
            target_id=str(target.id),
            message_ids=(str(message.id),),
        )
        await self._maybe_create_thread(message, plan.submission_id)
        logger.info(
            "Заявка %s отправлена в Discord #%s (%s)",
            plan.submission_id,
            target.id,
            plan.mode.value,
        )
        return result

    async def _maybe_create_thread(
        self, message: discord.Message, submission_id: int | None
    ) -> None:
        from bot.core.rules import display_sid
        from bot.settings import discord_publish_threads

        if not discord_publish_threads():
            return
        name = (
            f"Заявка {display_sid(submission_id)}"
            if submission_id is not None
            else "Обсуждение"
        )
        try:
            await message.create_thread(name=name[:100], auto_archive_duration=1440)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logger.info("Не удалось создать тред публикации: %s", exc)

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        client = self._require_client()
        channel = client.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await client.fetch_channel(int(channel_id))
            except (discord.HTTPException, discord.NotFound):
                return
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            message = await channel.fetch_message(int(message_id))
            await message.delete()
        except (discord.HTTPException, discord.NotFound):
            logger.warning(
                "Не удалось удалить Discord-сообщение %s/%s",
                channel_id,
                message_id,
            )

    def _with_emoji(self, caption: str) -> str:
        mark = self.emoji_mark.strip()
        if not mark:
            return caption
        if not caption:
            return mark
        if caption.startswith(mark):
            return caption
        return f"{mark} {caption}"

    def _resolve_channel(
        self, guild_id: str | None
    ) -> discord.TextChannel | None:
        client = self._require_client()
        config: GuildConfig | None = None
        if guild_id:
            config = self.guilds.get(str(guild_id))
        if config is None or not config.publish_channel_id:
            for candidate in self.guilds.list_all():
                if candidate.publish_channel_id:
                    config = candidate
                    break
        if config is None or not config.publish_channel_id:
            return None
        channel = client.get_channel(int(config.publish_channel_id))
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    def _require_client(self) -> discord.Client:
        if self._client is None:
            raise DiscordChannelPublishError("Discord client ещё не готов")
        return self._client

    async def _resolve_files(
        self, plan: PublishPlan, stack: AsyncExitStack
    ) -> list[discord.File]:
        files: list[discord.File] = []
        for item in plan.media:
            if item.content_type is ContentType.link:
                continue
            path = await self._materialize(item, stack)
            if path is None:
                continue
            files.append(discord.File(path, filename=path.name))
        return files

    async def _materialize(
        self, item: PublishMedia, stack: AsyncExitStack
    ) -> Path | None:
        if item.ref_kind is RefKind.local_path:
            path = Path(item.file_ref)
            return path if path.is_file() else None
        if item.ref_kind is RefKind.discord_url:
            return await self._download_url(item.file_ref, item, stack)
        if item.ref_kind is RefKind.telegram_file_id:
            return await self._download_telegram(item.file_ref, item, stack)
        return None

    async def _download_url(
        self, url: str, item: PublishMedia, stack: AsyncExitStack
    ) -> Path | None:
        tmp = stack.enter_context(
            tempfile.TemporaryDirectory(prefix="ds-publish-")
        )
        suffix = {
            ContentType.photo: ".jpg",
            ContentType.video: ".mp4",
            ContentType.sticker: ".webp",
        }.get(item.content_type, ".bin")
        path = Path(tmp) / f"file{suffix}"
        try:
            from bot.core.media_store import DOWNLOAD_HEADERS

            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SEC)
            async with aiohttp.ClientSession(
                timeout=timeout, headers=DOWNLOAD_HEADERS
            ) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.read()
            if len(data) > MAX_DOWNLOAD_BYTES:
                logger.warning("Вложение слишком большое для Discord")
                return None
            path.write_bytes(data)
            return path
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось скачать вложение %s", url)
            return None

    async def _download_telegram(
        self, file_id: str, item: PublishMedia, stack: AsyncExitStack
    ) -> Path | None:
        if self.telegram_bot is None:
            logger.warning("Нет Telegram bot для скачивания file_id")
            return None
        tmp = stack.enter_context(
            tempfile.TemporaryDirectory(prefix="ds-tg-")
        )
        suffix = {
            ContentType.photo: ".jpg",
            ContentType.video: ".mp4",
            ContentType.sticker: ".webp",
        }.get(item.content_type, ".bin")
        path = Path(tmp) / f"file{suffix}"
        try:
            file = await self.telegram_bot.get_file(file_id)
            await self.telegram_bot.download_file(file.file_path, destination=path)
            return path if path.is_file() else None
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось скачать Telegram file_id")
            return None
