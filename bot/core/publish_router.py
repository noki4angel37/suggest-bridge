"""Publish approved submissions to Telegram and/or Discord with retry/rollback."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from bot.core.models import (
    MirrorKind,
    MirrorLink,
    Platform,
    PublishTarget,
    Submission,
)
from bot.core.publisher import extract_publish_ref

logger = logging.getLogger(__name__)

PublishSideCallback = Callable[[Submission], Awaitable[object]]
DeleteSideCallback = Callable[[str, str], Awaitable[None]]

MIRROR_ENABLED_KEY = "mirror_enabled"
DEFAULT_RETRIES = 2
RETRY_DELAY_SEC = 0.5


class PublishRouterError(RuntimeError):
    pass


@dataclass(frozen=True)
class SidePublishResult:
    platform: Platform
    target_id: str
    message_id: str


@dataclass(frozen=True)
class DualPublishResult:
    """Combined result; primary fields stay Telegram-shaped for mark_published."""

    target_id: str
    message_id: str | None
    sides: tuple[SidePublishResult, ...] = ()

    @property
    def chat_id(self) -> str:
        return self.target_id

    @property
    def message_ids(self) -> tuple[str, ...]:
        return (self.message_id,) if self.message_id else ()


class PublishRouter:
    """Routes a submission by `publish_target`; records mirror_links on success."""

    def __init__(
        self,
        *,
        db: object,
        telegram_publish: PublishSideCallback | None = None,
        discord_publish: PublishSideCallback | None = None,
        telegram_delete: DeleteSideCallback | None = None,
        discord_delete: DeleteSideCallback | None = None,
        retries: int = DEFAULT_RETRIES,
        retry_delay_sec: float = RETRY_DELAY_SEC,
    ) -> None:
        self.db = db
        self.telegram_publish = telegram_publish
        self.discord_publish = discord_publish
        self.telegram_delete = telegram_delete
        self.discord_delete = discord_delete
        self.retries = retries
        self.retry_delay_sec = retry_delay_sec

    async def publish(self, submission: Submission) -> DualPublishResult:
        target = submission.publish_target or PublishTarget.both
        want_tg = target in (PublishTarget.telegram, PublishTarget.both)
        want_ds = target in (PublishTarget.discord, PublishTarget.both)

        if want_tg and self.telegram_publish is None:
            raise PublishRouterError("Telegram publisher не настроен")
        if want_ds and self.discord_publish is None:
            raise PublishRouterError(
                "Discord publisher не настроен или нет publish-канала"
            )

        sides: list[SidePublishResult] = []
        errors: list[str] = []

        if want_tg:
            assert self.telegram_publish is not None
            try:
                sides.append(
                    await self._publish_side(
                        Platform.telegram, self.telegram_publish, submission
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Telegram: {exc}")
                logger.exception(
                    "Публикация заявки %s в Telegram не удалась", submission.id
                )

        if want_ds:
            assert self.discord_publish is not None
            try:
                sides.append(
                    await self._publish_side(
                        Platform.discord, self.discord_publish, submission
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Discord: {exc}")
                logger.exception(
                    "Публикация заявки %s в Discord не удалась", submission.id
                )

        if errors:
            await self._rollback(sides)
            raise PublishRouterError("; ".join(errors))

        if not sides:
            raise PublishRouterError("Некуда публиковать: пустой publish_target")

        self._record_mirror(submission, sides)
        primary = next(
            (s for s in sides if s.platform is Platform.telegram), sides[0]
        )
        return DualPublishResult(
            target_id=primary.target_id,
            message_id=primary.message_id,
            sides=tuple(sides),
        )

    async def _publish_side(
        self,
        platform: Platform,
        callback: PublishSideCallback,
        submission: Submission,
    ) -> SidePublishResult:
        last_error: Exception | None = None
        attempts = max(1, self.retries + 1)
        for attempt in range(attempts):
            try:
                raw = await callback(submission)
                target_id, message_id = extract_publish_ref(raw)
                if not target_id or not message_id:
                    raise PublishRouterError(
                        f"{platform.value}: нет target_id/message_id"
                    )
                return SidePublishResult(
                    platform=platform,
                    target_id=target_id,
                    message_id=message_id,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.retry_delay_sec)
        assert last_error is not None
        raise last_error

    async def _rollback(self, sides: list[SidePublishResult]) -> None:
        for side in sides:
            try:
                if side.platform is Platform.telegram and self.telegram_delete:
                    await self.telegram_delete(side.target_id, side.message_id)
                elif side.platform is Platform.discord and self.discord_delete:
                    await self.discord_delete(side.target_id, side.message_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Rollback %s %s/%s не удался",
                    side.platform.value,
                    side.target_id,
                    side.message_id,
                )

    def _record_mirror(
        self, submission: Submission, sides: list[SidePublishResult]
    ) -> None:
        tg = next((s for s in sides if s.platform is Platform.telegram), None)
        ds = next((s for s in sides if s.platform is Platform.discord), None)
        if tg is None or ds is None:
            return
        insert = getattr(self.db, "insert_mirror_link", None)
        if insert is None:
            return
        guild_id = submission.guild_id
        insert(
            MirrorLink(
                origin=submission.source,
                kind=MirrorKind.suggest_publish,
                tg_chat_id=tg.target_id,
                tg_message_id=tg.message_id,
                ds_guild_id=guild_id,
                ds_channel_id=ds.target_id,
                ds_message_id=ds.message_id,
                submission_id=submission.id,
            )
        )


def is_mirror_enabled(db: object, *, default: bool = True) -> bool:
    getter = getattr(db, "get_setting", None)
    if getter is None:
        return default
    raw = getter(MIRROR_ENABLED_KEY)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


def set_mirror_enabled(db: object, enabled: bool) -> None:
    setter = getattr(db, "set_setting", None)
    if setter is None:
        return
    setter(MIRROR_ENABLED_KEY, "1" if enabled else "0")
