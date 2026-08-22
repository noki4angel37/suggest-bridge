"""Telegram /host panel: status + transfer inline buttons."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.adapters.admin_common import resolve_admin_services
from bot.adapters.telegram.handlers_settings import TelegramAdminFilter
from bot.core import Platform
from bot.core.host_control import (
    HostControlError,
    INSTALL_HINT,
    accept_request,
    append_audit,
    cancel_request,
    create_claim_request,
    create_offer_request,
    entry_is_online,
    find_hosts_for_admin,
    find_registry_host,
    get_request,
    is_owner_telegram,
    issue_start,
    issue_stop,
    list_pending,
    owner_force_to_host,
    owner_telegram_id,
    panel_snapshot,
    reject_request,
    require_discord_capable,
    resolve_host_id,
    stop_local_and_failover_owner,
)
from bot.core.host_sync import HostSyncStore

logger = logging.getLogger(__name__)


class HostCallback(CallbackData, prefix="host"):
    action: str
    ref: str = "-"  # request id, host id (~ for ':'), or confirm token


def _encode_ref(value: str) -> str:
    return value.replace(":", "~") if value else "-"


def _decode_ref(value: str) -> str:
    if not value or value == "-":
        return value
    return value.replace("~", ":")


def host_keyboard(
    *,
    is_owner: bool,
    pending_for_me: list[str],
    my_request_ids: list[str],
    registry_hosts: list[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Запросить primary себе",
                callback_data=HostCallback(action="claim", ref="-").pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="Предложить другому…",
                callback_data=HostCallback(action="offer_menu", ref="-").pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="Выключить на моём ПК",
                callback_data=HostCallback(action="stop_local", ref="-").pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="Как установить агент",
                callback_data=HostCallback(action="install", ref="-").pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=HostCallback(action="refresh", ref="-").pack(),
            )
        ],
    ]
    for req_id in my_request_ids[:5]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Отменить мой запрос {req_id[:8]}…",
                    callback_data=HostCallback(
                        action="cancel", ref=_encode_ref(req_id)
                    ).pack(),
                )
            ]
        )
    for req_id in pending_for_me[:5]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Принять {req_id[:8]}…",
                    callback_data=HostCallback(
                        action="accept", ref=_encode_ref(req_id)
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=HostCallback(
                        action="reject", ref=_encode_ref(req_id)
                    ).pack(),
                ),
            ]
        )
    if is_owner:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Force: забрать на мой ПК",
                    callback_data=HostCallback(action="force_mine", ref="-").pack(),
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Force: выбрать хост…",
                    callback_data=HostCallback(action="force_menu", ref="-").pack(),
                )
            ]
        )
        for index, host in enumerate(registry_hosts[:6], start=1):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Remote start ПК #{index}",
                        callback_data=HostCallback(
                            action="remote_start", ref=_encode_ref(host)
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text=f"Stop ПК #{index}",
                        callback_data=HostCallback(
                            action="remote_stop", ref=_encode_ref(host)
                        ).pack(),
                    ),
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def offer_pick_keyboard(hosts: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"ПК #{index}",
                callback_data=HostCallback(
                    action="offer", ref=_encode_ref(h)
                ).pack(),
            )
        ]
        for index, h in enumerate(hosts[:12], start=1)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="« Назад",
                callback_data=HostCallback(action="refresh", ref="-").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def force_confirm_keyboard(target_host: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Точно забрать / перенести",
                    callback_data=HostCallback(
                        action="force_yes", ref=_encode_ref(target_host)
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=HostCallback(action="refresh", ref="-").pack(),
                )
            ],
        ]
    )


def force_pick_keyboard(hosts: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"ПК #{index}",
                callback_data=HostCallback(
                    action="force_ask", ref=_encode_ref(h)
                ).pack(),
            )
        ]
        for index, h in enumerate(hosts[:12], start=1)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="« Назад",
                callback_data=HostCallback(action="refresh", ref="-").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


class HostPanelUI:
    def __init__(self, services: Any) -> None:
        self.services = resolve_admin_services(services)
        self.container = services
        self.sync = HostSyncStore()

    def _db(self):
        return self.services.admins.db

    def _actor_id(self, user_id: int) -> str:
        return str(user_id)

    def _is_owner(self, user_id: int) -> bool:
        return is_owner_telegram(user_id) or self.services.admins.is_owner(
            Platform.telegram, str(user_id)
        )

    def _panel_text_and_markup(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        snap = panel_snapshot(self._db(), self.sync)
        pending = list_pending(self._db())
        uid = str(user_id)
        pending_for_me: list[str] = []
        my_reqs: list[str] = []
        for req in pending:
            if req.from_admin == uid:
                my_reqs.append(req.id)
            # Current primary holder or owner accepts claim/offer targeting this primary.
            holder = snap.holder_admin
            if req.kind == "claim" and (
                (holder and holder == uid) or self._is_owner(user_id) or snap.is_primary
            ):
                if req.id not in pending_for_me:
                    pending_for_me.append(req.id)
            if req.kind == "offer" and (req.to_admin == uid or self._is_owner(user_id)):
                if req.id not in pending_for_me:
                    pending_for_me.append(req.id)
        hosts = [e.host_id for e in snap.registry if entry_is_online(e)]
        markup = host_keyboard(
            is_owner=self._is_owner(user_id),
            pending_for_me=pending_for_me,
            my_request_ids=my_reqs,
            registry_hosts=hosts,
        )
        return snap.format_msk(), markup

    async def cmd_host(self, message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        text, markup = self._panel_text_and_markup(user.id)
        await message.answer(text, reply_markup=markup)

    async def on_callback(self, callback: CallbackQuery, callback_data: HostCallback) -> None:
        user = callback.from_user
        if user is None:
            await callback.answer()
            return
        uid = self._actor_id(user.id)
        action = callback_data.action
        ref = _decode_ref(callback_data.ref)
        db = self._db()
        try:
            if action == "refresh":
                await self._edit_panel(callback, user.id)
                await callback.answer("Обновлено")
                return
            if action == "install":
                await callback.answer()
                if callback.message:
                    await callback.message.answer(INSTALL_HINT)
                return
            if action == "claim":
                hosts = find_hosts_for_admin(self.sync, telegram_id=uid)
                online = [h for h in hosts if entry_is_online(h)]
                if not online:
                    # Try local host id registration
                    local = find_registry_host(self.sync, resolve_host_id())
                    if not entry_is_online(local):
                        await callback.answer("Нужен агент", show_alert=True)
                        if callback.message:
                            await callback.message.answer(INSTALL_HINT)
                        return
                    online = [local]  # type: ignore[list-item]
                target = online[0]
                require_discord_capable(target)
                req = create_claim_request(
                    db, self.sync, admin_id=uid, target_host=target.host_id
                )
                await self._notify_parties(
                    f"Запрос primary: tg:{uid} → {target.host_id} ({req.id[:8]}…)"
                )
                await self._edit_panel(callback, user.id)
                await callback.answer("Запрос создан")
                return
            if action == "offer_menu":
                hosts = [
                    e.host_id
                    for e in self.sync.list_registry()
                    if entry_is_online(e) and e.has_discord
                ]
                if not hosts:
                    await callback.answer("Нет онлайн-хостов", show_alert=True)
                    return
                if isinstance(callback.message, Message):
                    await callback.message.edit_reply_markup(
                        reply_markup=offer_pick_keyboard(hosts)
                    )
                await callback.answer()
                return
            if action == "offer":
                req = create_offer_request(
                    db, self.sync, from_admin=uid, to_host=ref
                )
                await self._notify_parties(
                    f"Предложение primary: tg:{uid} предлагает {ref} ({req.id[:8]}…)"
                )
                await self._edit_panel(callback, user.id)
                await callback.answer("Предложение отправлено")
                return
            if action == "cancel":
                cancel_request(db, self.sync, request_id=ref, actor=f"tg:{uid}")
                await self._edit_panel(callback, user.id)
                await callback.answer("Отменено")
                return
            if action == "reject":
                reject_request(db, self.sync, request_id=ref, actor=f"tg:{uid}")
                await self._notify_parties(f"Запрос {ref[:8]}… отклонён tg:{uid}")
                await self._edit_panel(callback, user.id)
                await callback.answer("Отклонено")
                return
            if action == "accept":
                accept_request(db, self.sync, request_id=ref, actor=f"tg:{uid}")
                await self._notify_parties(
                    f"Запрос {ref[:8]}… принят — prepare на целевом ПК"
                )
                await self._edit_panel(callback, user.id)
                await callback.answer("Принято")
                return
            if action == "stop_local":
                msg = stop_local_and_failover_owner(
                    db, self.sync, actor=f"tg:{uid}"
                )
                await self._notify_parties(f"Stop local tg:{uid}: {msg}")
                if callback.message:
                    await callback.message.answer(msg)
                await self._edit_panel(callback, user.id)
                await callback.answer("Остановка")
                return
            if action == "force_mine":
                if not self._is_owner(user.id):
                    await callback.answer("Только супер-админ", show_alert=True)
                    return
                my_hosts = find_hosts_for_admin(self.sync, telegram_id=uid)
                online = [h for h in my_hosts if entry_is_online(h) and h.has_discord]
                if not online:
                    await callback.answer("Ваш агент офлайн", show_alert=True)
                    if callback.message:
                        await callback.message.answer(INSTALL_HINT)
                    return
                if isinstance(callback.message, Message):
                    await callback.message.edit_reply_markup(
                        reply_markup=force_confirm_keyboard(online[0].host_id)
                    )
                await callback.answer("Подтвердите")
                return
            if action == "force_menu":
                if not self._is_owner(user.id):
                    await callback.answer("Только супер-админ", show_alert=True)
                    return
                hosts = [
                    e.host_id
                    for e in self.sync.list_registry()
                    if entry_is_online(e) and e.has_discord
                ]
                if isinstance(callback.message, Message):
                    await callback.message.edit_reply_markup(
                        reply_markup=force_pick_keyboard(hosts)
                    )
                await callback.answer()
                return
            if action == "force_ask":
                if not self._is_owner(user.id):
                    await callback.answer("Только супер-админ", show_alert=True)
                    return
                if isinstance(callback.message, Message):
                    await callback.message.edit_reply_markup(
                        reply_markup=force_confirm_keyboard(ref)
                    )
                await callback.answer("Подтвердите")
                return
            if action == "force_yes":
                if not self._is_owner(user.id):
                    await callback.answer("Только супер-админ", show_alert=True)
                    return
                result = owner_force_to_host(
                    db,
                    self.sync,
                    target_host=ref,
                    actor=f"tg:{uid}",
                    confirmed=True,
                )
                await self._notify_parties(
                    f"FORCE tg:{uid} → {ref} ({result})"
                )
                await self._edit_panel(callback, user.id)
                await callback.answer("Force выполнен")
                return
            if action == "remote_start":
                if not self._is_owner(user.id):
                    await callback.answer("Только супер-админ", show_alert=True)
                    return
                issue_start(db, self.sync, host_id=ref, actor=f"tg:{uid}")
                await self._notify_parties(f"Remote start {ref} by tg:{uid}")
                await callback.answer("Start отправлен")
                return
            if action == "remote_stop":
                if not self._is_owner(user.id):
                    await callback.answer("Только супер-админ", show_alert=True)
                    return
                issue_stop(db, self.sync, host_id=ref, actor=f"tg:{uid}")
                await self._notify_parties(f"Remote stop {ref} by tg:{uid}")
                await callback.answer("Stop отправлен")
                return
            await callback.answer("Неизвестное действие")
        except HostControlError as exc:
            await callback.answer(str(exc)[:180], show_alert=True)
            if INSTALL_HINT[:40] in str(exc) and callback.message:
                await callback.message.answer(str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("host callback failed")
            await callback.answer("Ошибка", show_alert=True)

    async def _edit_panel(self, callback: CallbackQuery, user_id: int) -> None:
        if not isinstance(callback.message, Message):
            return
        text, markup = self._panel_text_and_markup(user_id)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            await callback.message.answer(text, reply_markup=markup)

    async def _notify_parties(self, text: str) -> None:
        """Best-effort DM to owner + audit; Discord mirror via optional hook."""
        bot = getattr(self.container, "bot", None)
        db = self._db()
        append_audit(db, actor="system", action="notify", detail=text[:300])
        owner = owner_telegram_id()
        if bot is not None and owner:
            try:
                await bot.send_message(int(owner), f"[host] {text}")
            except Exception:  # noqa: BLE001
                logger.warning("Не удалось уведомить владельца в TG")
        hook = getattr(self.container, "notify_host_audit", None)
        if callable(hook):
            try:
                await hook(text)
            except Exception:  # noqa: BLE001
                logger.warning("host audit hook failed")


def register_telegram_host(router: Any, services: Any) -> Router:
    resolved = resolve_admin_services(services)
    ui = HostPanelUI(services)

    host = Router(name="admin_host")
    host.message.filter(
        F.chat.type == ChatType.PRIVATE, TelegramAdminFilter(resolved)
    )
    host.callback_query.filter(TelegramAdminFilter(resolved))

    host.message.register(ui.cmd_host, Command("host"))
    host.callback_query.register(ui.on_callback, HostCallback.filter())

    router.include_router(host)
    return host
