"""Temporary Discord role grants requested by members and decided by mods."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.core.db import BridgeDatabase
from bot.core.models import PassRequest, PassRequestStatus, utcnow
from bot.core.pass_config import PassConfig, load_pass_config, resolve_pass_role_id

NowFn = Callable[[], datetime]
ClockFn = Callable[[], float]


def format_remaining(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} с"
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    if sec and minutes < 3:
        return f"{minutes} мин {sec} с"
    return f"{minutes} мин"


@dataclass(frozen=True)
class PassCreateResult:
    ok: bool
    reason: str
    message: str
    request: PassRequest | None = None
    retry_after_sec: float = 0.0


@dataclass(frozen=True)
class PassDecideResult:
    ok: bool
    reason: str
    message: str
    request: PassRequest | None = None


class PassService:
    """Guards, persistence and timers for /prohodka. No Discord I/O."""

    def __init__(
        self,
        db: BridgeDatabase,
        config: PassConfig | None = None,
        *,
        now: NowFn = utcnow,
        clock: ClockFn | None = None,
    ) -> None:
        self.db = db
        self.config = config or load_pass_config()
        self._now = now
        self._clock = clock or (lambda: self._now().timestamp())

    def role_id_for(self, guild_id: str) -> str | None:
        return resolve_pass_role_id(self.db, guild_id, self.config)

    def get(self, request_id: int) -> PassRequest | None:
        return self.db.get_pass_request(request_id)

    def list_pending(self, *, limit: int = 200) -> list[PassRequest]:
        return self.db.list_pass_requests(
            status=PassRequestStatus.pending, limit=limit
        )

    def list_approved(self, *, limit: int = 200) -> list[PassRequest]:
        return self.db.list_pass_requests(
            status=PassRequestStatus.approved, limit=limit
        )

    def due_grants(self) -> list[PassRequest]:
        return self.db.list_due_pass_grants(now=self._now())

    def save_mod_ref(
        self, request: PassRequest, *, channel_id: str, message_id: str
    ) -> PassRequest:
        request.mod_channel_id = channel_id
        request.mod_message_id = message_id
        return self.db.update_pass_request(request)

    def create_request(
        self,
        *,
        guild_id: str,
        user_id: str,
        display_name: str,
        username: str | None,
        blocked: bool,
        already_has_role: bool = False,
    ) -> PassCreateResult:
        if not self.role_id_for(guild_id):
            return PassCreateResult(
                ok=False,
                reason="disabled",
                message=(
                    "Проходка на этом сервере не настроена. "
                    "Админ может вызвать /setup_pass."
                ),
            )
        if blocked:
            return PassCreateResult(
                ok=False,
                reason="blocked",
                message="Вы не можете пользоваться этой командой.",
            )

        now = self._now()
        clock = self._clock()
        cfg = self.config
        last_hit, strike_until = self.db.peek_pass_antiflood(guild_id, user_id)
        if strike_until is not None and strike_until > clock:
            wait = strike_until - clock
            return PassCreateResult(
                ok=False,
                reason="antispam",
                message=(
                    "Слишком много попыток. Команда перезарядится через "
                    f"{format_remaining(wait)}."
                ),
                retry_after_sec=wait,
            )
        if last_hit and clock - last_hit < cfg.debounce_sec:
            wait = cfg.debounce_sec - (clock - last_hit)
            return PassCreateResult(
                ok=False,
                reason="debounce",
                message=f"Подождите {format_remaining(wait)} и попробуйте снова.",
                retry_after_sec=wait,
            )

        count, _, _ = self.db.bump_pass_antiflood(
            guild_id,
            user_id,
            now=clock,
            window_sec=cfg.antispam_window_sec,
        )
        if count > cfg.antispam_limit:
            strike = clock + cfg.antispam_strike_sec
            self.db.set_pass_strike(guild_id, user_id, strike_until=strike)
            return PassCreateResult(
                ok=False,
                reason="antispam",
                message=(
                    "Антиспам: слишком часто. Команда перезарядится через "
                    f"{format_remaining(cfg.antispam_strike_sec)}."
                ),
                retry_after_sec=float(cfg.antispam_strike_sec),
            )

        if already_has_role:
            return PassCreateResult(
                ok=False,
                reason="has_role",
                message=f"У вас уже есть роль «{cfg.label}».",
            )
        active = self.db.get_active_pass(guild_id, user_id, now=now)
        if active is not None and active.expires_at is not None:
            wait = (active.expires_at - now).total_seconds()
            return PassCreateResult(
                ok=False,
                reason="active",
                message=(
                    f"Проходка уже выдана, осталось {format_remaining(wait)}."
                ),
                retry_after_sec=wait,
            )
        pending = self.db.get_pending_pass(guild_id, user_id)
        if pending is not None:
            return PassCreateResult(
                ok=False,
                reason="pending",
                message="Заявка уже в модерации предложки — дождитесь решения.",
                request=pending,
            )
        cooling = self.db.get_reject_cooldown(guild_id, user_id, now=now)
        if cooling is not None and cooling.cooldown_until is not None:
            wait = (cooling.cooldown_until - now).total_seconds()
            return PassCreateResult(
                ok=False,
                reason="cooldown",
                message=(
                    "Заявка отклонена. Команда перезарядится через "
                    f"{format_remaining(wait)}."
                ),
                retry_after_sec=wait,
            )

        inserted = self.db.insert_pass_request(
            PassRequest(
                guild_id=str(guild_id),
                user_id=str(user_id),
                display_name=display_name,
                username=username,
            )
        )
        if inserted is None:
            existing = self.db.get_pending_pass(guild_id, user_id)
            return PassCreateResult(
                ok=False,
                reason="pending",
                message="Заявка уже в модерации предложки — дождитесь решения.",
                request=existing,
            )
        hours = cfg.duration_sec // 3600
        minutes = (cfg.duration_sec % 3600) // 60
        duration = f"{hours} ч" if not minutes else f"{hours} ч {minutes} мин"
        return PassCreateResult(
            ok=True,
            reason="created",
            message=(
                f"Заявка на «{cfg.label}» ({duration}) отправлена "
                "в модерацию предложки."
            ),
            request=inserted,
        )

    def abort(self, request_id: int) -> PassRequest | None:
        """Drop a pending request without cooldown (card failed to post)."""
        now = self._now()
        return self.db.claim_pass_decision(
            request_id,
            expected_status=PassRequestStatus.pending,
            new_status=PassRequestStatus.rejected,
            decided_by="abort",
            now=now,
            cooldown_until=now,
        )

    def reopen(self, request_id: int) -> PassRequest | None:
        """Undo approve when Discord could not grant the role."""
        current = self.db.get_pass_request(request_id)
        if current is None:
            return None
        now = self._now()
        return self.db.claim_pass_decision(
            request_id,
            expected_status=PassRequestStatus.approved,
            new_status=PassRequestStatus.pending,
            decided_by=current.decided_by or "reopen",
            now=now,
        )

    def approve(
        self, request_id: int, *, decided_by: str
    ) -> PassDecideResult:
        now = self._now()
        expires_at = now + timedelta(seconds=self.config.duration_sec)
        updated = self.db.claim_pass_decision(
            request_id,
            expected_status=PassRequestStatus.pending,
            new_status=PassRequestStatus.approved,
            decided_by=decided_by,
            now=now,
            expires_at=expires_at,
        )
        if updated is None:
            current = self.db.get_pass_request(request_id)
            if current is None:
                return PassDecideResult(
                    ok=False, reason="missing", message="Заявка не найдена."
                )
            return PassDecideResult(
                ok=False,
                reason="handled",
                message="Эту заявку уже разобрали.",
                request=current,
            )
        return PassDecideResult(
            ok=True,
            reason="approved",
            message=(
                f"Выдана «{self.config.label}» на "
                f"{format_remaining(self.config.duration_sec)}."
            ),
            request=updated,
        )

    def reject(
        self, request_id: int, *, decided_by: str
    ) -> PassDecideResult:
        now = self._now()
        cooldown_until = now + timedelta(seconds=self.config.reject_cooldown_sec)
        updated = self.db.claim_pass_decision(
            request_id,
            expected_status=PassRequestStatus.pending,
            new_status=PassRequestStatus.rejected,
            decided_by=decided_by,
            now=now,
            cooldown_until=cooldown_until,
        )
        if updated is None:
            current = self.db.get_pass_request(request_id)
            if current is None:
                return PassDecideResult(
                    ok=False, reason="missing", message="Заявка не найдена."
                )
            return PassDecideResult(
                ok=False,
                reason="handled",
                message="Эту заявку уже разобрали.",
                request=current,
            )
        return PassDecideResult(
            ok=True,
            reason="rejected",
            message=(
                "Заявка отклонена. Команда перезарядится через "
                f"{format_remaining(self.config.reject_cooldown_sec)}."
            ),
            request=updated,
        )

    def expire(self, request_id: int) -> PassRequest | None:
        current = self.db.get_pass_request(request_id)
        if current is None:
            return None
        now = self._now()
        return self.db.claim_pass_decision(
            request_id,
            expected_status=PassRequestStatus.approved,
            new_status=PassRequestStatus.expired,
            decided_by=current.decided_by or "expiry",
            now=now,
            expires_at=current.expires_at,
        )
