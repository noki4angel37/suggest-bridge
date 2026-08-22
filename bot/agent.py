"""Always-on suggest host agent (no Telegram getUpdates).

Watches HOST_SYNC_DIR for commands and manages local bot.main processes:
prepare (standby warm), go_primary, start, stop.

Run: python -m bot.agent
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

from bot.config import bootstrap_env
from bot.core.host_control import owner_discord_id, owner_telegram_id, resolve_host_id
from bot.core.host_sync import (
    HostAck,
    HostCommand,
    HostRegistryEntry,
    HostSyncStore,
    resolve_sync_dir,
)

logger = logging.getLogger(__name__)

BOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_HEARTBEAT_SEC = 20.0
DEFAULT_POLL_SEC = 1.5


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _has_discord_token() -> bool:
    bootstrap_env()
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    return bool(token) and token != "REPLACE_ME"


def _admin_telegram_id() -> str | None:
    bootstrap_env()
    raw = os.environ.get("AGENT_TELEGRAM_ID", "").strip()
    if raw:
        return raw
    # Prefer owner if this machine is owner's; else first ADMIN_IDS.
    admins = os.environ.get("ADMIN_IDS", "").strip()
    if admins:
        return admins.split(",")[0].strip() or None
    return owner_telegram_id()


def _admin_discord_id() -> str | None:
    bootstrap_env()
    raw = os.environ.get("AGENT_DISCORD_ID", "").strip()
    if raw:
        return raw
    return owner_discord_id()


class SuggestAgent:
    def __init__(self, sync: HostSyncStore | None = None) -> None:
        self.sync = sync or HostSyncStore()
        self.host_id = resolve_host_id()
        self._bot_proc: subprocess.Popen[str] | None = None
        self._role: str | None = None
        self._stop = False

    def _python(self) -> str:
        venv = BOT_DIR / ".venv" / "Scripts" / "python.exe"
        if venv.is_file():
            return str(venv)
        venv_unix = BOT_DIR / ".venv" / "bin" / "python"
        if venv_unix.is_file():
            return str(venv_unix)
        return sys.executable

    def write_heartbeat(self) -> None:
        entry = HostRegistryEntry(
            host_id=self.host_id,
            admin_telegram_id=_admin_telegram_id(),
            admin_discord_id=_admin_discord_id(),
            has_discord=_has_discord_token(),
            agent_online=True,
            os_name=f"{platform.system()} {platform.release()}",
            bot_role=self._role,
        )
        self.sync.write_registry(entry)

    def _spawn_bot(self, role: str) -> None:
        self._stop_bot()
        env = os.environ.copy()
        env["HOST_ID"] = self.host_id
        env["HOST_ROLE"] = role
        env.setdefault("HOST_SYNC_DIR", str(resolve_sync_dir()))
        env["HOST_SKIP_SRV1_HANDOFF"] = "1"
        if role == "primary":
            env["HOST_FORCE_CLAIM"] = "1"
        admin = _admin_telegram_id()
        if admin:
            env["HOST_HOLDER_ADMIN"] = admin
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        self._bot_proc = subprocess.Popen(
            [self._python(), "-m", "bot.main"],
            cwd=str(BOT_DIR),
            env=env,
            creationflags=creationflags,
        )
        self._role = role
        logger.info(
            "Started bot.main role=%s pid=%s HOST_ID=%s",
            role,
            self._bot_proc.pid,
            self.host_id,
        )

    def _stop_bot(self) -> None:
        proc = self._bot_proc
        if proc is None:
            self._role = None
            return
        if proc.poll() is None:
            logger.info("Stopping bot.main pid=%s", proc.pid)
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    proc.terminate()
            except OSError:
                proc.kill()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._bot_proc = None
        self._role = "stopped"

    def _ack(self, command: HostCommand, *, ok: bool, detail: str = "") -> None:
        self.sync.write_ack(
            self.host_id,
            HostAck(
                action=command.action,
                ok=ok,
                detail=detail,
                request_id=command.request_id,
            ),
        )
        self.sync.clear_command(self.host_id)

    def handle_command(self, command: HostCommand) -> None:
        action = command.action.strip().lower()
        logger.info("Command %s req=%s", action, command.request_id)
        try:
            if action == "prepare":
                self._spawn_bot("standby")
                # Standby process itself acks prepare when warm; do not double-ack.
                time.sleep(1.0)
                if self._bot_proc is None or self._bot_proc.poll() is not None:
                    self._ack(command, ok=False, detail="standby_failed")
                else:
                    # Leave command cleared only after standby ack; clear spawn cmd
                    # so standby wait loop is not confused by leftover prepare.
                    self.sync.clear_command(self.host_id)
                    logger.info("standby spawned; waiting for process prepare ack")
                return
            elif action == "go_primary":
                # Standby bot.main watches the same command file; do not respawn.
                if self._role == "standby" and self._bot_proc is not None:
                    if self._bot_proc.poll() is None:
                        logger.info(
                            "go_primary: standby process will claim (pid=%s)",
                            self._bot_proc.pid,
                        )
                        # Leave command for standby; heartbeat only.
                        self.write_heartbeat()
                        return
                self._spawn_bot("primary")
                # Force claim on handover conflicts.
                if self._bot_proc is not None:
                    # CHILD already got env; set for future spawns.
                    os.environ["HOST_FORCE_CLAIM"] = "1"
                time.sleep(2.0)
                ok = self._bot_proc is not None and self._bot_proc.poll() is None
                self._ack(
                    command,
                    ok=ok,
                    detail="primary_started" if ok else "primary_failed",
                )
                self._role = "primary" if ok else "stopped"
            elif action == "start":
                self._spawn_bot("primary")
                time.sleep(2.0)
                ok = self._bot_proc is not None and self._bot_proc.poll() is None
                self._ack(
                    command, ok=ok, detail="started" if ok else "start_failed"
                )
            elif action == "stop":
                self._stop_bot()
                self._ack(command, ok=True, detail="stopped")
            else:
                self._ack(command, ok=False, detail=f"unknown_action:{action}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Command failed")
            self._ack(command, ok=False, detail=str(exc)[:200])
        self.write_heartbeat()

    def poll_once(self) -> None:
        if self._bot_proc is not None and self._bot_proc.poll() is not None:
            code = self._bot_proc.returncode
            logger.warning("bot.main exited with %s", code)
            self._bot_proc = None
            self._role = "stopped"
        command = self.sync.read_command(self.host_id)
        if command and command.action:
            self.handle_command(command)
        self.write_heartbeat()

    async def run(self) -> int:
        poll = _env_float("HOST_AGENT_POLL_SEC", DEFAULT_POLL_SEC)
        logger.info(
            "suggest-agent HOST_ID=%s sync=%s",
            self.host_id,
            self.sync.root,
        )
        self.write_heartbeat()
        while not self._stop:
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001
                logger.exception("agent poll failed")
            await asyncio.sleep(poll)
        self._stop_bot()
        return 0

    def request_stop(self) -> None:
        self._stop = True


async def _amain() -> int:
    bootstrap_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not os.environ.get("HOST_ID", "").strip():
        os.environ["HOST_ID"] = resolve_host_id()
    agent = SuggestAgent()

    loop = asyncio.get_running_loop()

    def _stop(*_args: object) -> None:
        agent.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _stop())

    return await agent.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suggest bot host agent")
    parser.parse_args(argv)
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
