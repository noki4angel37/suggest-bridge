"""Build a standalone suggest-bot zip for admins (no secrets, no .venv).

Used by /download (Telegram) and /download_bot (Discord). Mirrors the
exclusions of scripts/pack-suggest-bot.ps1.
"""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EXCLUDE_DIR_NAMES = frozenset(
    {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "data",
        ".git",
    }
)
EXCLUDE_FILE_NAMES = frozenset(
    {
        "local.env",
        ".env",
        ".coverage",
        "bridge.db",
        "submissions.db",
    }
)
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo"})

REQUIRED_RELATIVE = (
    "bot/agent.py",
    "bot/main.py",
    "install-agent.ps1",
    "run-agent.ps1",
    ".env.example",
    "requirements.txt",
    "SETUP.md",
)

# Telegram bot sendDocument limit is 50 MiB; stay under with margin.
MAX_ZIP_BYTES = 45 * 1024 * 1024


def package_root() -> Path:
    """Package root directory (parent of the `bot` package)."""
    # bot/core/pack_dist.py → core → bot → package root
    return Path(__file__).resolve().parent.parent.parent


def _should_skip(path: Path, *, root: Path) -> bool:
    rel = path.relative_to(root)
    for part in rel.parts[:-1]:
        if part in EXCLUDE_DIR_NAMES:
            return True
    if path.is_dir():
        return path.name in EXCLUDE_DIR_NAMES
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def iter_package_files(root: Path | None = None) -> list[Path]:
    base = root or package_root()
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip(path, root=base):
            continue
        files.append(path)
    return files


def build_suggest_bot_zip(
    out_dir: Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Write suggest-bot-YYYYMMDD-HHMM.zip and return its path."""
    base = root or package_root()
    for rel in REQUIRED_RELATIVE:
        if not (base / rel).is_file():
            raise FileNotFoundError(f"Package incomplete: missing {rel}")

    dest = out_dir or (base / "data" / "dist")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    zip_path = dest / f"suggest-bot-{stamp}.zip"
    latest = dest / "suggest-bot-latest.zip"

    files = iter_package_files(base)
    if not files:
        raise FileNotFoundError("Package has no files to zip")

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as zf:
        for path in files:
            arcname = path.relative_to(base).as_posix()
            zf.write(path, arcname)

    size = zip_path.stat().st_size
    if size > MAX_ZIP_BYTES:
        zip_path.unlink(missing_ok=True)
        raise ValueError(
            f"Package too large for bot upload ({size} bytes > {MAX_ZIP_BYTES})"
        )

    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.write_bytes(zip_path.read_bytes())
    except OSError:
        logger.warning("Could not refresh %s", latest)

    logger.info("Packed suggest-bot zip: %s (%s bytes)", zip_path, size)
    return zip_path
