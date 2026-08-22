"""Process entry point (`python -m bot.main`).

Runs the Telegram ↔ Discord suggest bridge on one bridge DB.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from bot.bridge import run_bridge
from bot.config import bootstrap_env

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def main() -> int:
    setup_logging()
    bootstrap_env()
    return await run_bridge()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
