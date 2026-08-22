"""Persistent media cache for Discord CDN attachments (ingest-time download).

Discord deletes the suggest-channel message immediately; CDN URLs then expire
within about a day. We download into ``data/media/{submission_id}/`` next to
the bridge DB and store ``RefKind.local_path`` so delayed publish still works.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp

from bot.core.db import resolve_bridge_db_path
from bot.core.models import ContentType, MediaItem, RefKind

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 120
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_EXTENSIONS: dict[ContentType, str] = {
    ContentType.photo: ".jpg",
    ContentType.video: ".mp4",
    ContentType.sticker: ".webp",
    ContentType.link: ".bin",
}


def media_root(*, db_path: str | None = None) -> Path:
    """``<parent of bridge.db>/media`` (persistent media cache)."""
    root = Path(db_path or resolve_bridge_db_path()).expanduser().resolve()
    return root.parent / "media"


def submission_dir(submission_id: int, *, db_path: str | None = None) -> Path:
    return media_root(db_path=db_path) / str(int(submission_id))


def delete_submission_files(
    submission_id: int, *, db_path: str | None = None
) -> None:
    """Remove cached files for a draft; ignore missing directories."""
    folder = submission_dir(submission_id, db_path=db_path)
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.warning("Не удалось удалить кэш %s", path)
    try:
        folder.rmdir()
    except OSError:
        logger.debug("Каталог кэша %s не пуст или уже удалён", folder)


def _suffix_for(content_type: ContentType, url: str, filename: str | None) -> str:
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix:
            return suffix
    path = unquote(urlparse(url).path)
    suffix = Path(path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return _EXTENSIONS.get(content_type, ".bin")


def _safe_stem(filename: str | None, order_index: int) -> str:
    raw = (filename or f"file_{order_index}").rsplit(".", 1)[0]
    cleaned = _UNSAFE_NAME.sub("_", raw).strip("._") or f"file_{order_index}"
    return cleaned[:64]


# Discord CDN often rejects generic HTTP clients with 403.
DOWNLOAD_HEADERS = {
    "User-Agent": "DiscordBot (https://github.com/Rapptz/discord.py, 2.4)",
    "Accept": "*/*",
}


def store_bytes(
    submission_id: int,
    data: bytes,
    *,
    order_index: int,
    content_type: ContentType,
    filename: str | None = None,
    db_path: str | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path | None:
    """Write already-fetched bytes into the submission media folder."""
    if not data:
        return None
    if len(data) > max_bytes:
        logger.warning(
            "Вложение слишком большое для кэша (%s bytes)", len(data)
        )
        return None
    folder = submission_dir(submission_id, db_path=db_path)
    folder.mkdir(parents=True, exist_ok=True)
    name = _safe_stem(filename, order_index)
    suffix = Path(filename or "").suffix.lower() or _EXTENSIONS.get(
        content_type, ".bin"
    )
    dest = folder / f"{order_index:02d}_{name}{suffix}"
    dest.write_bytes(data)
    return dest


async def fetch_url_bytes(
    url: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout_sec: float = DOWNLOAD_TIMEOUT_SEC,
) -> bytes | None:
    """GET ``url`` with a Discord bot User-Agent; None on failure/oversize."""
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(
            timeout=timeout, headers=DOWNLOAD_HEADERS
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.read()
        if len(data) > max_bytes:
            logger.warning(
                "Вложение слишком большое для кэша (%s bytes)", len(data)
            )
            return None
        return data
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось скачать вложение: %s", url)
        return None


async def download_url(
    url: str,
    dest: Path,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout_sec: float = DOWNLOAD_TIMEOUT_SEC,
) -> Path | None:
    """Download ``url`` into ``dest``; returns path or None on failure/oversize."""
    data = await fetch_url_bytes(
        url, max_bytes=max_bytes, timeout_sec=timeout_sec
    )
    if data is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


async def materialize_discord_media(
    submission_id: int,
    items: list[MediaItem],
    *,
    db_path: str | None = None,
) -> list[MediaItem]:
    """Replace Discord CDN refs with ``local_path``; leave other refs alone."""
    if not items:
        return items
    folder = submission_dir(submission_id, db_path=db_path)
    result: list[MediaItem] = []
    for item in sorted(items, key=lambda m: m.order_index):
        if item.ref_kind is not RefKind.discord_url or not item.discord_attachment_url:
            result.append(item)
            continue
        url = item.discord_attachment_url
        name = _safe_stem(None, item.order_index)
        suffix = _suffix_for(item.content_type, url, None)
        dest = folder / f"{item.order_index:02d}_{name}{suffix}"
        path = await download_url(url, dest)
        if path is None:
            # Keep the CDN URL as a last resort for immediate publish.
            result.append(item)
            continue
        result.append(
            MediaItem(
                content_type=item.content_type,
                order_index=item.order_index,
                local_path=str(path),
                caption=item.caption,
            )
        )
    return result


def materialize_from_blobs(
    submission_id: int,
    items: list[MediaItem],
    blobs: dict[int, bytes],
    *,
    db_path: str | None = None,
) -> list[MediaItem]:
    """Replace items with matching ``order_index`` blobs by ``local_path``."""
    result: list[MediaItem] = []
    for item in sorted(items, key=lambda m: m.order_index):
        data = blobs.get(item.order_index)
        if not data:
            result.append(item)
            continue
        path = store_bytes(
            submission_id,
            data,
            order_index=item.order_index,
            content_type=item.content_type,
            db_path=db_path,
        )
        if path is None:
            result.append(item)
            continue
        result.append(
            MediaItem(
                content_type=item.content_type,
                order_index=item.order_index,
                local_path=str(path),
                caption=item.caption,
            )
        )
    return result
