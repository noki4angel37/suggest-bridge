"""Post short product updates to the guild bot-news channel."""

from __future__ import annotations

import logging
import os

import discord

logger = logging.getLogger(__name__)

# Public community changelog channel (readable by everyone).
DEFAULT_BOT_NEWS_CHANNEL_ID = "1542481352644104193"
NEWS_COLOR = 0x5865F2


def resolve_bot_news_channel_id() -> str | None:
    raw = (os.environ.get("DISCORD_BOT_NEWS_CHANNEL_ID") or "").strip()
    if raw and raw != "REPLACE_ME":
        return raw
    return DEFAULT_BOT_NEWS_CHANNEL_ID


async def post_bot_news(
    bot: discord.Client,
    *,
    text: str,
    title: str = "Обновление бота",
) -> discord.Message:
    """Send a short embed to the configured news channel."""
    channel_id = resolve_bot_news_channel_id()
    if not channel_id:
        raise RuntimeError("DISCORD_BOT_NEWS_CHANNEL_ID is not set")
    body = (text or "").strip()
    if not body:
        raise ValueError("empty news text")
    if len(body) > 1800:
        body = body[:1797] + "…"

    channel = bot.get_channel(int(channel_id))
    if channel is None:
        channel = await bot.fetch_channel(int(channel_id))
    if not isinstance(channel, discord.abc.Messageable):
        raise RuntimeError(f"channel {channel_id} is not messageable")

    embed = discord.Embed(
        title=title[:256],
        description=body,
        color=NEWS_COLOR,
    )
    return await channel.send(embed=embed)
