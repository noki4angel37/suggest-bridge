"""Execute a core :class:`PublishPlan` in a Telegram channel via aiogram."""

from __future__ import annotations

import logging
import re
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp
from aiogram import Bot
from aiogram.types import (
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from bot.core.models import ContentType, RefKind, Submission
from bot.core.publisher import (
    PublishMedia,
    PublishMode,
    PublishPlan,
    build_publish_plan,
)

logger = logging.getLogger(__name__)

# Telegram limits: 10 items per media group, 50 MB per bot upload.
ALBUM_CHUNK_SIZE = 10
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 120
DOWNLOAD_CHUNK_BYTES = 64 * 1024

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_EXTENSIONS: dict[ContentType, str] = {
    ContentType.photo: ".jpg",
    ContentType.video: ".mp4",
    ContentType.sticker: ".webp",
}

# aiogram accepts either a plain file_id/URL string or an uploadable file.
TelegramInput = str | FSInputFile


class ChannelPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishResult:
    target_id: str
    message_ids: tuple[str, ...] = ()

    @property
    def chat_id(self) -> str:
        """Telegram-flavoured alias of `target_id`."""
        return self.target_id

    @property
    def message_id(self) -> str | None:
        return self.message_ids[0] if self.message_ids else None


class TelegramChannelPublisher:
    """Sends submissions to one channel; usable as a scheduler callback."""

    def __init__(
        self,
        bot: Bot,
        channel_id: int | str,
        *,
        parse_mode: str | None = None,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
        download_timeout_sec: float = DOWNLOAD_TIMEOUT_SEC,
    ) -> None:
        self.bot = bot
        self.channel_id = channel_id
        self.parse_mode = parse_mode
        self.max_download_bytes = max_download_bytes
        self.download_timeout_sec = download_timeout_sec

    async def __call__(self, submission: Submission) -> PublishResult:
        return await self.publish(submission)

    async def publish(
        self,
        submission: Submission,
        *,
        with_author: bool | None = None,
        author_line_override: str | None = None,
    ) -> PublishResult:
        plan = build_publish_plan(
            submission,
            with_author=with_author,
            author_line_override=author_line_override,
        )
        return await self.publish_plan(plan)

    async def publish_plan(self, plan: PublishPlan) -> PublishResult:
        async with AsyncExitStack() as stack:
            inputs = await self._resolve_inputs(plan, stack)
            message_ids: list[str] = []

            if plan.mode is PublishMode.text:
                message_ids.append(await self._send_text(plan.caption))
            elif plan.mode is PublishMode.single:
                message_ids.extend(
                    await self._send_single(plan.media[0], inputs)
                )
            else:
                message_ids.extend(await self._send_album(plan, inputs))

            if plan.caption_as_separate_message and plan.caption:
                message_ids.append(await self._send_text(plan.caption))

        result = PublishResult(
            target_id=str(self.channel_id), message_ids=tuple(message_ids)
        )
        logger.info(
            "Заявка %s отправлена в канал %s (%s, сообщений: %d)",
            plan.submission_id,
            self.channel_id,
            plan.mode.value,
            len(result.message_ids),
        )
        return result

    # --- sending -------------------------------------------------------------

    async def _send_text(self, text: str) -> str:
        message = await self.bot.send_message(
            self.channel_id, text, parse_mode=self.parse_mode
        )
        return str(message.message_id)

    async def _send_single(
        self, item: PublishMedia, inputs: dict[PublishMedia, TelegramInput]
    ) -> list[str]:
        payload = inputs[item]
        caption = item.caption
        if item.content_type is ContentType.photo:
            message = await self.bot.send_photo(
                self.channel_id,
                payload,
                caption=caption,
                parse_mode=self.parse_mode,
            )
        elif item.content_type is ContentType.video:
            message = await self.bot.send_video(
                self.channel_id,
                payload,
                caption=caption,
                parse_mode=self.parse_mode,
            )
        elif item.content_type is ContentType.sticker:
            message = await self.bot.send_sticker(self.channel_id, payload)
        else:
            message = await self.bot.send_document(
                self.channel_id,
                payload,
                caption=caption,
                parse_mode=self.parse_mode,
            )
        return [str(message.message_id)]

    async def _send_album(
        self, plan: PublishPlan, inputs: dict[PublishMedia, TelegramInput]
    ) -> list[str]:
        message_ids: list[str] = []
        album = plan.album_items
        for start in range(0, len(album), ALBUM_CHUNK_SIZE):
            chunk = album[start : start + ALBUM_CHUNK_SIZE]
            group = [self._to_input_media(item, inputs) for item in chunk]
            sent: list[Message] = await self.bot.send_media_group(
                self.channel_id, media=group
            )
            message_ids.extend(str(message.message_id) for message in sent)
        for item in plan.standalone_items:
            message_ids.extend(await self._send_single(item, inputs))
        return message_ids

    def _to_input_media(
        self, item: PublishMedia, inputs: dict[PublishMedia, TelegramInput]
    ) -> InputMediaPhoto | InputMediaVideo:
        payload = inputs[item]
        if item.content_type is ContentType.video:
            return InputMediaVideo(
                media=payload, caption=item.caption, parse_mode=self.parse_mode
            )
        return InputMediaPhoto(
            media=payload, caption=item.caption, parse_mode=self.parse_mode
        )

    # --- media refs ----------------------------------------------------------

    async def _resolve_inputs(
        self, plan: PublishPlan, stack: AsyncExitStack
    ) -> dict[PublishMedia, TelegramInput]:
        """Map each media item to a file_id, a local file or a downloaded copy."""
        inputs: dict[PublishMedia, TelegramInput] = {}
        session: aiohttp.ClientSession | None = None
        temp_dir: Path | None = None

        for index, item in enumerate(plan.media):
            if not item.needs_download:
                inputs[item] = _local_input(item)
                continue
            if session is None:
                timeout = aiohttp.ClientTimeout(total=self.download_timeout_sec)
                session = await stack.enter_async_context(
                    aiohttp.ClientSession(timeout=timeout)
                )
                temp_dir = Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(prefix="suggest-publish-")
                    )
                )
            assert temp_dir is not None
            path = await self._download(
                session, item, temp_dir / _safe_name(item, index)
            )
            inputs[item] = FSInputFile(str(path))
        return inputs

    async def _download(
        self, session: aiohttp.ClientSession, item: PublishMedia, dest: Path
    ) -> Path:
        logger.info("Скачиваю вложение Discord: %s", item.file_ref)
        try:
            from bot.core.media_store import DOWNLOAD_HEADERS

            async with session.get(
                item.file_ref, headers=DOWNLOAD_HEADERS
            ) as response:
                response.raise_for_status()
                written = 0
                with dest.open("wb") as handle:
                    async for chunk in response.content.iter_chunked(
                        DOWNLOAD_CHUNK_BYTES
                    ):
                        written += len(chunk)
                        if written > self.max_download_bytes:
                            raise ChannelPublishError(
                                "Вложение больше лимита загрузки Telegram: "
                                f"{item.file_ref}"
                            )
                        handle.write(chunk)
        except aiohttp.ClientError as error:
            raise ChannelPublishError(
                f"Не удалось скачать вложение {item.file_ref}: {error}"
            ) from error
        return dest


async def publish_submission(
    bot: Bot,
    channel_id: int | str,
    submission: Submission,
    *,
    with_author: bool | None = None,
) -> PublishResult:
    """One-shot helper for adapters that do not keep a publisher instance."""
    publisher = TelegramChannelPublisher(bot, channel_id)
    return await publisher.publish(submission, with_author=with_author)


def _local_input(item: PublishMedia) -> TelegramInput:
    if item.ref_kind is RefKind.local_path:
        return FSInputFile(item.file_ref)
    return item.file_ref


def _safe_name(item: PublishMedia, index: int) -> str:
    name = Path(unquote(urlparse(item.file_ref).path)).name
    name = _UNSAFE_NAME.sub("_", name).strip("._")
    if not name or "." not in name:
        extension = _EXTENSIONS.get(item.content_type, ".bin")
        name = f"media_{index}{extension}"
    return name[:120]
