"""Optional HTTP health endpoint for Docker/systemd probes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

AiohttpRequest = Any
AiohttpResponse = Any


def health_port() -> int | None:
    raw = os.environ.get("HEALTH_PORT", "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    return port if port > 0 else None


async def build_health_payload(
    *,
    telegram_ok: Callable[[], bool] | None = None,
    discord_ok: Callable[[], bool] | None = None,
    lease_ok: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    if telegram_ok is not None:
        checks["telegram"] = telegram_ok()
    if discord_ok is not None:
        checks["discord"] = discord_ok()
    if lease_ok is not None:
        checks["lease"] = lease_ok()
    ok = all(checks.values()) if checks else True
    return {"status": "ok" if ok else "degraded", "checks": checks}


async def start_health_server(
    *,
    telegram_ok: Callable[[], bool] | None = None,
    discord_ok: Callable[[], bool] | None = None,
    lease_ok: Callable[[], bool] | None = None,
) -> asyncio.Task[None] | None:
    port = health_port()
    if port is None:
        return None
    try:
        from aiohttp import web
    except ImportError:
        logger.warning("aiohttp unavailable; HEALTH_PORT ignored")
        return None

    async def handle(_request: web.Request) -> web.Response:
        payload = await build_health_payload(
            telegram_ok=telegram_ok,
            discord_ok=discord_ok,
            lease_ok=lease_ok,
        )
        code = 200 if payload["status"] == "ok" else 503
        return web.Response(
            text=json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
            status=code,
        )

    app = web.Application()
    app.router.add_get("/healthz", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health endpoint http://0.0.0.0:%s/healthz", port)

    async def _run() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await runner.cleanup()
            raise

    return asyncio.create_task(_run(), name="health-server")
