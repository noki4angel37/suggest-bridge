"""Safe HTTP fetch helpers with CDN allowlist and log redaction."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

ALLOWED_FETCH_HOSTS = frozenset(
    {
        "cdn.discordapp.com",
        "media.discordapp.net",
        "api.telegram.org",
    }
)

DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_TIMEOUT_SEC = 120.0

DOWNLOAD_HEADERS = {
    "User-Agent": "SuggestBridge/1.0 (+https://github.com/noki4angel37/suggest-bridge)",
}


class UnsafeUrlError(ValueError):
    """URL rejected by allowlist or private-network guard."""


def redact_url_for_log(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return "<invalid-url>"
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def validate_fetch_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"unsupported scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeUrlError("missing host")
    if host not in ALLOWED_FETCH_HOSTS:
        raise UnsafeUrlError(f"host not allowlisted: {host}")
    _reject_private_ip(host)


def _reject_private_ip(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise UnsafeUrlError(f"private/reserved address: {addr}")


async def fetch_url_bytes(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    headers: dict[str, str] | None = None,
) -> bytes | None:
    """GET allowlisted ``url``; None on failure/oversize/rejection."""
    try:
        validate_fetch_url(url)
    except UnsafeUrlError:
        logger.warning("Rejected fetch URL: %s", redact_url_for_log(url))
        return None
    merged = dict(DOWNLOAD_HEADERS)
    if headers:
        merged.update(headers)
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(
            timeout=timeout, headers=merged
        ) as session:
            async with session.get(url, allow_redirects=False) as response:
                response.raise_for_status()
                data = await response.read()
        if len(data) > max_bytes:
            logger.warning(
                "Attachment too large (%s bytes) from %s",
                len(data),
                redact_url_for_log(url),
            )
            return None
        return data
    except Exception:  # noqa: BLE001
        logger.exception("Download failed: %s", redact_url_for_log(url))
        return None
