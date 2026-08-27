"""Bidirectional TG ↔ Discord channel mirror with dedup via mirror_links."""

from __future__ import annotations

import logging
import re
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path

import aiohttp
import discord
from aiogram import Bot, Router
from aiogram.types import Message as TgMessage
from discord.ext import commands

from bot.adapters.discord.channel_publish import DiscordChannelPublisher
from bot.core.db import BridgeDatabase
from bot.core.models import MirrorKind, MirrorLink, Platform
from bot.core.publish_router import is_mirror_enabled, set_mirror_enabled
from bot.core.services import GuildConfigService

logger = logging.getLogger(__name__)

VOICE_CONTENT_TYPES = frozenset({"voice", "video_note"})
INFO_POST_TEXT = (
    "🔗 Интеграция Telegram ↔ Discord\n\n"
    "Посты из Telegram-канала дублируются сюда, и наоборот — "
    "без повторов. Голосовые сообщения не переносятся.\n\n"
    "Одобренные предложки тоже публикуются в эту ленту."
)
TG_PREFIX = "📣 из Discord"
DS_PREFIX = "📣 из Telegram"


def _info_post_text(telegram_channel_id: int | str) -> str:
    from bot.adapters.discord import texts as ds_texts

    url = ds_texts.telegram_channel_public_url(telegram_channel_id)
    if not url:
        return INFO_POST_TEXT
    return f"{INFO_POST_TEXT}\n\nКанал Telegram: {url}"


class ChannelMirrorService:
    """Shared TG↔DS mirror operations used by both adapters."""

    def __init__(
        self,
        db: BridgeDatabase,
        *,
        telegram_bot: Bot,
        telegram_channel_id: int | str,
        guilds: GuildConfigService,
        discord_publisher: DiscordChannelPublisher | None = None,
    ) -> None:
        self.db = db
        self.telegram_bot = telegram_bot
        self.telegram_channel_id = str(telegram_channel_id)
        self.guilds = guilds
        self.discord_publisher = discord_publisher
        self._discord_client: discord.Client | None = None

    def bind_discord(self, client: discord.Client) -> None:
        self._discord_client = client
        if self.discord_publisher is not None:
            self.discord_publisher.bind_client(client)

    def enabled(self) -> bool:
        return is_mirror_enabled(self.db, default=True)

    def set_enabled(self, enabled: bool) -> None:
        set_mirror_enabled(self.db, enabled)

    async def post_info_announcement(self) -> None:
        """One-shot info posts in TG channel and DS publish channel."""
        text = _info_post_text(self.telegram_channel_id)
        try:
            tg_msg = await self.telegram_bot.send_message(
                self.telegram_channel_id, text
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить инфо-пост в Telegram")
            tg_msg = None

        ds_msg = None
        channel = self._primary_publish_channel()
        if channel is not None:
            try:
                ds_msg = await channel.send(text)
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось отправить инфо-пост в Discord")

        if tg_msg is not None and ds_msg is not None:
            self.db.insert_mirror_link(
                MirrorLink(
                    origin=Platform.telegram,
                    kind=MirrorKind.channel_mirror,
                    tg_chat_id=str(self.telegram_channel_id),
                    tg_message_id=str(tg_msg.message_id),
                    ds_guild_id=str(ds_msg.guild.id) if ds_msg.guild else None,
                    ds_channel_id=str(ds_msg.channel.id),
                    ds_message_id=str(ds_msg.id),
                )
            )

    async def mirror_telegram_to_discord(self, message: TgMessage) -> None:
        if not self.enabled():
            return
        if str(message.chat.id) != str(self.telegram_channel_id):
            return
        if message.voice or message.video_note:
            logger.info("Пропуск голосового TG %s", message.message_id)
            return
        if self.db.find_mirror_by_tg(
            str(message.chat.id), str(message.message_id)
        ):
            return

        channel = self._primary_publish_channel()
        if channel is None:
            return

        content, files = await self._tg_to_discord_payload(message)
        try:
            if files:
                sent = await channel.send(content=content or None, files=files)
            else:
                if not content:
                    return
                sent = await channel.send(content=content)
        except Exception:  # noqa: BLE001
            logger.exception("Зеркало TG→DS не удалось")
            return

        self.db.insert_mirror_link(
            MirrorLink(
                origin=Platform.telegram,
                kind=MirrorKind.channel_mirror,
                tg_chat_id=str(message.chat.id),
                tg_message_id=str(message.message_id),
                ds_guild_id=str(sent.guild.id) if sent.guild else None,
                ds_channel_id=str(sent.channel.id),
                ds_message_id=str(sent.id),
            )
        )

    async def edit_telegram_mirror(self, message: TgMessage) -> None:
        """Propagate TG edits to the Discord copy only (never rewrite Discord originals)."""
        if not self.enabled():
            return
        link = self.db.find_mirror_by_tg(
            str(message.chat.id), str(message.message_id)
        )
        if link is None or not link.ds_channel_id or not link.ds_message_id:
            return
        # Origin Discord ⇒ TG message is the copy; a later TG edit must not
        # clobber the Discord original through this path either.
        if link.origin is Platform.discord:
            return
        client = self._discord_client
        if client is None:
            return
        channel = client.get_channel(int(link.ds_channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            ds_msg = await channel.fetch_message(int(link.ds_message_id))
            text = format_discord_mirror_text(message)
            await ds_msg.edit(content=text or ds_msg.content)
        except Exception:  # noqa: BLE001
            logger.exception("Правка зеркала TG→DS не удалась")

    async def delete_telegram_mirror(
        self, chat_id: str, message_id: str
    ) -> None:
        link = self.db.find_mirror_by_tg(str(chat_id), str(message_id))
        if link is None:
            return
        if link.ds_channel_id and link.ds_message_id and self._discord_client:
            channel = self._discord_client.get_channel(int(link.ds_channel_id))
            if isinstance(channel, discord.TextChannel):
                try:
                    msg = await channel.fetch_message(int(link.ds_message_id))
                    await msg.delete()
                except Exception:  # noqa: BLE001
                    logger.exception("Удаление зеркала TG→DS не удалось")
        if link.id is not None:
            self.db.delete_mirror_link(link.id)

    async def mirror_discord_to_telegram(self, message: discord.Message) -> None:
        if not self.enabled():
            return
        if message.author.bot:
            return
        if not self._is_publish_channel(message.channel.id):
            return
        if any(
            getattr(att, "content_type", None)
            and str(att.content_type).startswith("audio/")
            for att in message.attachments
        ):
            # Skip obvious voice-like attachments when Discord marks them audio.
            if message.attachments and all(
                (att.content_type or "").startswith("audio/")
                for att in message.attachments
            ):
                logger.info("Пропуск голосового DS %s", message.id)
                return
        if self.db.find_mirror_by_ds(str(message.channel.id), str(message.id)):
            return

        text = format_telegram_mirror_text(message.content or "")
        try:
            if message.attachments:
                sent = await self._send_tg_with_attachments(message, text)
            elif text:
                sent = await self.telegram_bot.send_message(
                    self.telegram_channel_id, text
                )
            else:
                return
        except Exception:  # noqa: BLE001
            logger.exception("Зеркало DS→TG не удалось")
            return

        self.db.insert_mirror_link(
            MirrorLink(
                origin=Platform.discord,
                kind=MirrorKind.channel_mirror,
                tg_chat_id=str(self.telegram_channel_id),
                tg_message_id=str(sent.message_id),
                ds_guild_id=str(message.guild.id) if message.guild else None,
                ds_channel_id=str(message.channel.id),
                ds_message_id=str(message.id),
            )
        )

    async def edit_discord_mirror(self, message: discord.Message) -> None:
        """Propagate Discord edits to the TG copy only — never edit TG originals."""
        if not self.enabled():
            return
        if message.author.bot:
            return
        link = self.db.find_mirror_by_ds(
            str(message.channel.id), str(message.id)
        )
        if link is None or not link.tg_chat_id or not link.tg_message_id:
            return
        # Origin Telegram ⇒ TG message is the user's original channel post.
        # Discord embed refreshes used to rewrite that post — never do that.
        if link.origin is not Platform.discord:
            return
        text = format_telegram_mirror_text(message.content or "")
        try:
            await self.telegram_bot.edit_message_text(
                text or " ",
                chat_id=link.tg_chat_id,
                message_id=int(link.tg_message_id),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Правка зеркала DS→TG не удалась")

    async def delete_discord_mirror(
        self, channel_id: str, message_id: str
    ) -> None:
        link = self.db.find_mirror_by_ds(str(channel_id), str(message_id))
        if link is None:
            return
        if link.tg_chat_id and link.tg_message_id:
            # Only delete TG twins that the bot created from Discord.
            if link.origin is Platform.discord:
                try:
                    await self.telegram_bot.delete_message(
                        chat_id=link.tg_chat_id,
                        message_id=int(link.tg_message_id),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Удаление зеркала DS→TG не удалось")
        if link.id is not None:
            self.db.delete_mirror_link(link.id)

    async def repost_from_telegram(
        self, tg_message_id: int | None = None
    ) -> bool:
        """Manual repost notice of a TG channel message into Discord (no links)."""
        if tg_message_id is None:
            return False
        try:
            channel = self._primary_publish_channel()
            if channel is None:
                return False
            sent = await channel.send(
                f"{DS_PREFIX}\n(ручной repost #{tg_message_id})"
            )
            self.db.insert_mirror_link(
                MirrorLink(
                    origin=Platform.telegram,
                    kind=MirrorKind.channel_mirror,
                    tg_chat_id=str(self.telegram_channel_id),
                    tg_message_id=str(tg_message_id),
                    ds_guild_id=str(sent.guild.id) if sent.guild else None,
                    ds_channel_id=str(sent.channel.id),
                    ds_message_id=str(sent.id),
                )
            )
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Ручной repost TG→DS не удался")
            return False

    def _primary_publish_channel(self) -> discord.TextChannel | None:
        if self._discord_client is None:
            return None
        for config in self.guilds.list_all():
            if not config.publish_channel_id:
                continue
            channel = self._discord_client.get_channel(
                int(config.publish_channel_id)
            )
            if isinstance(channel, discord.TextChannel):
                return channel
        return None

    def _is_publish_channel(self, channel_id: int) -> bool:
        needle = str(channel_id)
        return any(
            config.publish_channel_id == needle
            for config in self.guilds.list_all()
        )

    def _format_ds_text(self, message: TgMessage) -> str:
        return format_discord_mirror_text(message)

    def _format_tg_text(self, message: discord.Message) -> str:
        return format_telegram_mirror_text(message.content or "")

    async def _tg_to_discord_payload(
        self, message: TgMessage
    ) -> tuple[str, list[discord.File]]:
        content = format_discord_mirror_text(message)
        files: list[discord.File] = []
        file_id = None
        filename = "file.bin"
        if message.photo:
            file_id = message.photo[-1].file_id
            filename = "photo.jpg"
        elif message.video:
            file_id = message.video.file_id
            filename = "video.mp4"
        elif message.document:
            file_id = message.document.file_id
            filename = message.document.file_name or "document.bin"
        elif message.sticker:
            file_id = message.sticker.file_id
            filename = "sticker.webp"
        if file_id:
            async with AsyncExitStack() as stack:
                tmp = stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="mirror-tg-")
                )
                path = Path(tmp) / filename
                try:
                    file = await self.telegram_bot.get_file(file_id)
                    await self.telegram_bot.download_file(
                        file.file_path, destination=path
                    )
                    data = path.read_bytes()
                except Exception:  # noqa: BLE001
                    logger.exception("Скачивание TG для зеркала не удалось")
                    data = b""
            if data:
                from io import BytesIO

                files.append(
                    discord.File(BytesIO(data), filename=filename)
                )
        return content, files

    async def _send_tg_with_attachments(
        self, message: discord.Message, text: str
    ) -> TgMessage:
        att = message.attachments[0]
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(att.url) as response:
                response.raise_for_status()
                data = await response.read()
        from aiogram.types import BufferedInputFile

        upload = BufferedInputFile(data, filename=att.filename or "file.bin")
        ctype = (att.content_type or "").lower()
        if ctype.startswith("image/"):
            return await self.telegram_bot.send_photo(
                self.telegram_channel_id, upload, caption=text or None
            )
        if ctype.startswith("video/"):
            return await self.telegram_bot.send_video(
                self.telegram_channel_id, upload, caption=text or None
            )
        return await self.telegram_bot.send_document(
            self.telegram_channel_id, upload, caption=text or None
        )


DISCORD_MIRROR_LIMIT = 2000
TELEGRAM_MIRROR_CAPTION_LIMIT = 1024


def _truncate_mirror(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_discord_mirror_text(message: TgMessage) -> str:
    """TG → Discord body: source mark + text only (no cross-post URLs)."""
    body = (message.text or message.caption or "").strip()
    body = re.sub(rf"^{re.escape(DS_PREFIX)}\s*", "", body)
    body = re.sub(rf"^{re.escape(TG_PREFIX)}\s*", "", body)
    # Drop leftover auto-appended cross links from older bot versions.
    lines = [
        line
        for line in body.splitlines()
        if not _is_cross_post_link(line.strip())
    ]
    body = "\n".join(lines).strip()
    parts = [DS_PREFIX]
    if body:
        parts.append(body)
    return _truncate_mirror("\n".join(parts), DISCORD_MIRROR_LIMIT)


def format_telegram_mirror_text(content: str) -> str:
    """Discord → TG body: source mark + text only (no jump_url / t.me links)."""
    body = (content or "").strip()
    body = re.sub(rf"^{re.escape(DS_PREFIX)}\s*", "", body)
    body = re.sub(rf"^{re.escape(TG_PREFIX)}\s*", "", body)
    # Drop leftover auto-appended cross links from older bot versions.
    lines = [
        line
        for line in body.splitlines()
        if not _is_cross_post_link(line.strip())
    ]
    body = "\n".join(lines).strip()
    parts = [TG_PREFIX]
    if body:
        parts.append(body)
    return _truncate_mirror("\n".join(parts), TELEGRAM_MIRROR_CAPTION_LIMIT)


def _is_cross_post_link(line: str) -> bool:
    if not line:
        return False
    return bool(
        re.match(r"^https://(t\.me/|discord\.com/channels/)", line)
    )


def build_telegram_mirror_router(mirror: ChannelMirrorService) -> Router:
    router = Router(name="channel_mirror")

    @router.channel_post()
    async def on_channel_post(message: TgMessage) -> None:
        await mirror.mirror_telegram_to_discord(message)

    @router.edited_channel_post()
    async def on_edited_channel_post(message: TgMessage) -> None:
        await mirror.edit_telegram_mirror(message)

    # Bot API does not deliver channel post deletions to bots in general.
    # Manual cleanup: Discord twin can be removed via /repost workflows;
    # DS→TG delete remains wired in MirrorCog.on_message_delete.
    return router


class MirrorCog(commands.Cog, name="mirror"):
    def __init__(
        self, bot: commands.Bot, mirror: ChannelMirrorService
    ) -> None:
        self.bot = bot
        self.mirror = mirror

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        await self.mirror.mirror_discord_to_telegram(message)

    @commands.Cog.listener()
    async def on_message_edit(
        self, _before: discord.Message, after: discord.Message
    ) -> None:
        if after.guild is None or after.author.bot:
            return
        await self.mirror.edit_discord_mirror(after)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        await self.mirror.delete_discord_mirror(
            str(message.channel.id), str(message.id)
        )
