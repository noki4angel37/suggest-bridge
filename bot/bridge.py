"""Composition root: Telegram + Discord + scheduler in one asyncio process.

Both adapters share one `BridgeDatabase`, one `EventBus` and one set of core
services. Publishing goes through `PublishRouter` (TG and/or Discord) with
retry/rollback. Channel mirror keeps TG CHANNEL_ID and DS publish channel in sync.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage

from bot.adapters.discord.channel_publish import DiscordChannelPublisher
from bot.adapters.discord.context import ServiceBundle
from bot.adapters.discord.mirror import (
    ChannelMirrorService,
    build_telegram_mirror_router,
)
from bot.adapters.telegram.deps import TelegramServices
from bot.adapters.telegram.event_sync import notify_author
from bot.adapters.telegram.router import build_telegram_router
from bot.config import BridgeConfig, RunMode, load_bridge_config
from bot.core import BridgeDatabase, EventBus, Scheduler, Submission
from bot.core.host_lease import (
    HostLeaseError,
    claim,
    heartbeat_loop,
    release,
    resolve_host_id,
)
from bot.core.host_control import (
    control_loop,
    mark_primary_started,
    mirror_state,
    clear_primary_markers,
)
from bot.core.host_sync import HostSyncStore
from bot.core.module_loader import ModuleRegistry, enforce_strict_load
from bot.core.modules import ModuleContext
from bot.core.publish_router import PublishRouter

logger = logging.getLogger(__name__)

TELEGRAM_POLL_RETRY_SEC = 20.0


def is_retryable_telegram_poll_error(exc: BaseException) -> bool:
    """Network blips should not tear down Discord. Bad token should."""
    if isinstance(exc, TelegramUnauthorizedError):
        return False
    if isinstance(exc, TelegramNetworkError):
        return True
    return False


def _host_role() -> str:
    raw = os.environ.get("HOST_ROLE", "primary").strip().lower()
    return raw if raw in {"primary", "standby"} else "primary"


@dataclass
class Bridge:
    """Wired runtime; built without any network call, so it is unit-testable."""

    config: BridgeConfig
    db: BridgeDatabase
    bus: EventBus
    services: Any
    scheduler: Scheduler
    publish_router: PublishRouter
    discord_publisher: DiscordChannelPublisher
    mirror: ChannelMirrorService
    modules: ModuleRegistry
    bot: Bot | None = None
    dp: Dispatcher | None = None
    _discord_client: object | None = field(default=None, repr=False)

    def module_context(
        self,
        *,
        discord_bot: object | None = None,
        discord_ctx: object | None = None,
    ) -> ModuleContext:
        return ModuleContext(
            config=self.config,
            db=self.db,
            bus=self.bus,
            services=self.services,
            logger=logger,
            telegram_bot=self.bot,
            dp=self.dp,
            discord_bot=discord_bot,
            discord_ctx=discord_ctx,
        )

    async def publish_now(self, submission: Submission) -> object:
        """Publish hook for adapters: approve → dual-publish router."""
        return await self.publish_router.publish(submission)

    async def notify_telegram_author(
        self, submission: Submission, text: str
    ) -> bool:
        """Notify hook: a Discord moderator replies to a Telegram author."""
        if self.bot is None:
            return False
        return await notify_author(self.bot, submission, text)

    async def run_discord(self) -> None:
        """Discord adapter loop; its failure must not stop the Telegram bot."""
        token = self.config.discord_token
        if not token:
            logger.warning("DISCORD_TOKEN не задан — Discord-адаптер выключен")
            return
        from bot.adapters.discord import start_discord

        backoff_sec = 5.0
        while True:
            try:
                await start_discord(
                    token,
                    self.services,
                    publish=self.publish_now,
                    notify_telegram_author=self.notify_telegram_author,
                    on_bot_ready=self._on_discord_ready,
                    mirror=self.mirror,
                    telegram_bot=self.bot,
                    module_registry=self.modules,
                    module_context_factory=self.module_context,
                )
                logger.warning(
                    "Discord-адаптер завершился без ошибки — переподключение через %.0f с",
                    backoff_sec,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Discord-адаптер упал — Telegram продолжает; "
                    "переподключение через %.0f с",
                    backoff_sec,
                )
            await asyncio.sleep(backoff_sec)

    async def _on_discord_ready(self, client: object) -> None:
        self._discord_client = client
        self.discord_publisher.bind_client(client)  # type: ignore[arg-type]
        self.mirror.bind_discord(client)  # type: ignore[arg-type]


def build_bridge(config: BridgeConfig, *, bot: Bot | None = None) -> Bridge:
    """Wire adapters, services and the scheduler on one DB and one bus."""
    db = BridgeDatabase(config.bridge_db_path)
    bus = EventBus()
    telegram_enabled = config.telegram_enabled

    if telegram_enabled:
        assert config.bot_token is not None
        telegram_bot = bot if bot is not None else Bot(token=config.bot_token)
        services = TelegramServices.from_database(
            db,
            bot=telegram_bot,
            channel_id=config.channel_id or 0,
            bus=bus,
        )
        seeded = services.admins.bootstrap_telegram_admins(config.admin_ids)
        logger.info("Админы Telegram из конфига: %s", len(seeded))
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(build_telegram_router(services, attach_events=False))
        services.events.attach(bus)
    else:
        telegram_bot = None
        services = ServiceBundle.from_db(db, bus=bus)
        dp = None

    from bot.core.event_log import attach_event_log

    attach_event_log(bus)

    assert services.guilds is not None
    discord_publisher = DiscordChannelPublisher(
        services.guilds, telegram_bot=telegram_bot
    )

    async def telegram_publish(submission: Submission) -> object:
        if not telegram_enabled or telegram_bot is None:
            raise RuntimeError("Telegram publish requested but Telegram is disabled")
        return await services.publisher.publish(submission)

    async def telegram_delete(chat_id: str, message_id: str) -> None:
        if telegram_bot is None:
            return
        try:
            await telegram_bot.delete_message(
                chat_id=int(chat_id), message_id=int(message_id)
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось удалить TG-сообщение %s/%s при rollback",
                chat_id,
                message_id,
            )

    router = PublishRouter(
        db=db,
        telegram_publish=telegram_publish if telegram_enabled else None,
        discord_publish=discord_publisher.publish,
        telegram_delete=telegram_delete if telegram_enabled else None,
        discord_delete=discord_publisher.delete_message,
    )
    if hasattr(services, "publish_router"):
        services.publish_router = router

    mirror = ChannelMirrorService(
        db,
        telegram_bot=telegram_bot,
        telegram_channel_id=config.channel_id if telegram_enabled else None,
        guilds=services.guilds,
        discord_publisher=discord_publisher,
    )
    if hasattr(services, "mirror"):
        services.mirror = mirror

    if dp is not None:
        dp.include_router(build_telegram_mirror_router(mirror))

    owners = services.admins.bootstrap_owner_admins()
    if owners:
        logger.info("Админы из OWNER_*: %s", len(owners))

    scheduler = Scheduler(db, services.moderation, router.publish, bus=bus)

    modules = ModuleRegistry()
    summary = modules.load_from_env()
    enforce_strict_load(summary)

    return Bridge(
        config=config,
        db=db,
        bus=bus,
        bot=telegram_bot,
        dp=dp,
        services=services,
        scheduler=scheduler,
        publish_router=router,
        discord_publisher=discord_publisher,
        mirror=mirror,
        modules=modules,
    )


async def _wait_standby_go_primary(host_id: str, sync: HostSyncStore) -> bool:
    """Warm process: wait until agent/primary issues go_primary for this host."""
    from bot.core.host_sync import HostAck

    logger.info("HOST_ROLE=standby — ожидание go_primary для %s", host_id)
    sync.write_ack(
        host_id,
        HostAck(action="prepare", ok=True, detail="standby_process_ready"),
    )
    deadline = asyncio.get_running_loop().time() + float(
        os.environ.get("HOST_STANDBY_WAIT_SEC", "120")
    )
    while asyncio.get_running_loop().time() < deadline:
        cmd = sync.read_command(host_id)
        if cmd and cmd.action == "go_primary":
            sync.clear_command(host_id)
            return True
        if cmd and cmd.action == "stop":
            sync.write_ack(
                host_id, HostAck(action="stop", ok=True, detail="standby_aborted")
            )
            sync.clear_command(host_id)
            return False
        await asyncio.sleep(0.5)
    logger.error("standby timeout waiting for go_primary")
    return False


async def _setup_telegram_commands(bot: Bot) -> None:
    from aiogram.types import BotCommand

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начать / предложка"),
                BotCommand(command="host", description="Кто держит бота / передача ПК"),
                BotCommand(
                    command="download",
                    description="Скачать пакет бота для нового ПК",
                ),
                BotCommand(command="adminhelp", description="Справка админа"),
                BotCommand(command="host_status", description="Статус host lease"),
                BotCommand(command="mirror", description="Зеркало TG↔Discord"),
            ]
        )
        logger.info("Telegram BotCommand menu обновлено")
    except Exception:  # noqa: BLE001
        logger.exception("set_my_commands failed")


async def run_bridge(config: BridgeConfig | None = None) -> int:
    """Run the bridge. Returns a process exit code (0 = clean)."""
    cfg = config or load_bridge_config()
    bridge = build_bridge(cfg)
    host_id = resolve_host_id()
    role = _host_role()
    sync = HostSyncStore()
    logger.info(
        "Мост запускается: mode=%s БД=%s HOST_ID=%s ROLE=%s",
        cfg.run_mode.value,
        bridge.config.bridge_db_path,
        host_id,
        role,
    )

    if role == "standby":
        ok = await _wait_standby_go_primary(host_id, sync)
        if not ok:
            if bridge.bot is not None:
                await bridge.bot.session.close()
            return 0

    telegram_enabled = cfg.telegram_enabled
    discord_enabled = cfg.discord_enabled

    if telegram_enabled:
        try:
            claim(bridge.db, host_id)
        except HostLeaseError as exc:
            if os.environ.get("HOST_FORCE_CLAIM", "").strip() in {"1", "true", "yes"}:
                from bot.core.host_control import force_claim

                force_claim(bridge.db, host_id)
                logger.warning("Force-claimed lease for HOST_ID=%s", host_id)
            else:
                logger.error("%s", exc)
                if bridge.bot is not None:
                    await bridge.bot.session.close()
                return exc.exit_code

    holder = os.environ.get("HOST_HOLDER_ADMIN", "").strip() or None
    mark_primary_started(bridge.db, host_id, holder_admin=holder)
    mirror_state(bridge.db, sync)

    if telegram_enabled and bridge.bot is not None:
        await _setup_telegram_commands(bridge.bot)

    module_ctx = bridge.module_context()
    await bridge.modules.setup_all(module_ctx)
    if telegram_enabled and bridge.dp is not None:
        await bridge.modules.setup_telegram_all(module_ctx)

    await bridge.scheduler.start()
    heartbeat_task: asyncio.Task[None] | None = None
    if telegram_enabled:
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(bridge.db, host_id), name="host-lease-heartbeat"
        )
    control_task = asyncio.create_task(
        control_loop(bridge.db, sync, host_id=host_id), name="host-control-loop"
    )
    discord_task: asyncio.Task[None] | None = None
    if discord_enabled:
        discord_task = asyncio.create_task(
            bridge.run_discord(), name="discord-adapter"
        )

    health_task: asyncio.Task[None] | None = None
    try:
        from bot.health import start_health_server

        def _telegram_ok() -> bool:
            return bool(telegram_enabled and bridge.bot is not None)

        def _discord_ok() -> bool:
            if not discord_enabled:
                return True
            return bridge._discord_client is not None

        def _lease_ok() -> bool:
            if not telegram_enabled:
                return True
            from bot.core.host_lease import is_primary as is_host_primary

            return is_host_primary(bridge.db, host_id)

        def _modules_ok() -> bool:
            return bridge.modules.health_ok()

        health_task = await start_health_server(
            telegram_ok=_telegram_ok if telegram_enabled else None,
            discord_ok=_discord_ok if discord_enabled else None,
            lease_ok=_lease_ok if telegram_enabled else None,
            modules_ok=_modules_ok,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Health server failed to start")

    try:
        if telegram_enabled and bridge.dp is not None and bridge.bot is not None:
            while True:
                try:
                    await bridge.dp.start_polling(bridge.bot)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    if not is_retryable_telegram_poll_error(exc):
                        raise
                    logger.exception(
                        "Telegram polling: сеть недоступна, повтор через %s с",
                        TELEGRAM_POLL_RETRY_SEC,
                    )
                    await asyncio.sleep(TELEGRAM_POLL_RETRY_SEC)
        elif discord_task is not None:
            await discord_task
        else:
            logger.error("No platform adapters enabled")
            return 1
    finally:
        if health_task is not None:
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        control_task.cancel()
        if heartbeat_task is not None:
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        try:
            await control_task
        except asyncio.CancelledError:
            pass
        if telegram_enabled:
            release(bridge.db, host_id)
        clear_primary_markers(bridge.db)
        mirror_state(bridge.db, sync)
        await _shutdown(bridge, discord_task)
    return 0


async def _shutdown(
    bridge: Bridge,
    discord_task: asyncio.Task[None] | None,
) -> None:
    bridge.modules.merge_context(discord_bot=bridge._discord_client)
    await bridge.modules.teardown_all()
    if discord_task is not None:
        discord_task.cancel()
        try:
            await discord_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Discord-адаптер завершился с ошибкой")
    await bridge.scheduler.stop()
    if bridge.bot is not None:
        await bridge.bot.session.close()
    logger.info("Мост остановлен")


async def main() -> int:
    return await run_bridge()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
