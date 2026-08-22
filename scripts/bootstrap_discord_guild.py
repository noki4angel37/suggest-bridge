"""One-shot: create suggest/mod channels and save GuildConfig to bridge.db.

Usage (from package dir, with local.env filled):
  .\\.venv\\Scripts\\python.exe scripts\\bootstrap_discord_guild.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord

from bot.adapters.discord import texts
from bot.adapters.discord.guild_setup import (
    ensure_suggest_category,
    place_in_category,
)
from bot.config import load_bridge_config
from bot.core import BridgeDatabase, GuildConfigService, resolve_bridge_db_path


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bootstrap_discord")


async def ensure_suggest(guild: discord.Guild) -> discord.TextChannel:
    from bot.adapters.discord.guild_decorate import is_suggest_channel_name

    for ch in guild.text_channels:
        if is_suggest_channel_name(ch.name):
            return ch
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            add_reactions=True,
        )
    }
    channel = await guild.create_text_channel(
        texts.SETUP_SUGGEST_CHANNEL_NAME,
        overwrites=overwrites,
        topic=texts.SETUP_INTRO[:1024],
        reason="bootstrap предложки",
    )
    await channel.send(texts.SETUP_INTRO)
    return channel


async def ensure_mod(guild: discord.Guild, me: discord.Member) -> discord.TextChannel:
    for ch in guild.text_channels:
        if ch.name == texts.SETUP_MOD_CHANNEL_NAME:
            return ch
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            add_reactions=True,
        ),
    }
    # Guild owner / manage_guild members still need access via role later;
    # give @admins-like: anyone with administrator will see via Discord's overrides.
    for role in guild.roles:
        if role.permissions.administrator or role.permissions.manage_guild:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                add_reactions=True,
            )
    return await guild.create_text_channel(
        texts.SETUP_MOD_CHANNEL_NAME,
        overwrites=overwrites,
        reason="bootstrap модерации предложки",
    )


async def ensure_publish(
    guild: discord.Guild, me: discord.Member
) -> discord.TextChannel:
    from bot.adapters.discord.guild_decorate import (
        channel_slug,
        is_publish_channel_name,
    )

    category = await ensure_suggest_category(guild)
    preferred: discord.TextChannel | None = None
    legacy: discord.TextChannel | None = None
    for ch in guild.text_channels:
        if not is_publish_channel_name(ch.name):
            continue
        if channel_slug(ch.name) == texts.SETUP_PUBLISH_CHANNEL_NAME.casefold():
            preferred = ch
            break
        if legacy is None:
            legacy = ch
    if preferred is not None:
        await place_in_category(preferred, category)
        return preferred
    if legacy is not None:
        await place_in_category(legacy, category)
        return legacy
    editor = None
    for role in guild.roles:
        if role.name == texts.SETUP_EDITOR_ROLE_NAME:
            editor = role
            break
    if editor is None:
        editor = await guild.create_role(
            name=texts.SETUP_EDITOR_ROLE_NAME,
            reason="bootstrap редакторов ленты",
            mentionable=True,
        )
    from bot.adapters.discord.guild_setup import publish_channel_overwrites

    overwrites = publish_channel_overwrites(guild, editor)
    overwrites[me] = discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        manage_messages=True,
        attach_files=True,
        embed_links=True,
    )
    channel = await guild.create_text_channel(
        texts.SETUP_PUBLISH_CHANNEL_NAME,
        overwrites=overwrites,
        topic=texts.setup_publish_intro()[:1024],
        category=category,
        reason="bootstrap ленты публикации",
    )
    await channel.send(texts.setup_publish_intro())
    return channel


async def main() -> int:
    cfg = load_bridge_config()
    if not cfg.discord_token:
        log.error("DISCORD_TOKEN не задан в local.env")
        return 1

    db = BridgeDatabase(resolve_bridge_db_path())
    guilds = GuildConfigService(db)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.guilds = True
    client = discord.Client(intents=intents)

    ready = asyncio.Event()

    @client.event
    async def on_ready() -> None:
        ready.set()

    await client.login(cfg.discord_token)
    connect_task = asyncio.create_task(client.connect(reconnect=False))
    try:
        await asyncio.wait_for(ready.wait(), timeout=60)
    except TimeoutError:
        log.error("Discord не ответил за 60 с (проверьте intents и токен)")
        await client.close()
        return 1

    assert client.user is not None
    if not client.guilds:
        log.error("Бот не на одном сервере — сначала пригласите его")
        await client.close()
        return 1

    for guild in client.guilds:
        me = guild.me
        if me is None:
            log.warning("Нет Member для guild %s — пропуск", guild.name)
            continue
        log.info("Настраиваю сервер: %s (%s)", guild.name, guild.id)
        try:
            suggest = await ensure_suggest(guild)
            mod = await ensure_mod(guild, me)
            publish = await ensure_publish(guild, me)
        except discord.Forbidden:
            log.exception(
                "Нет прав Manage Channels на сервере %s — выдайте боту права",
                guild.name,
            )
            continue
        guilds.set_channels(
            str(guild.id),
            suggest_channel_id=str(suggest.id),
            mod_channel_id=str(mod.id),
            publish_channel_id=str(publish.id),
        )
        log.info(
            "OK %s → заявок #%s, модерация #%s, публикация #%s",
            guild.name,
            suggest.name,
            mod.name,
            publish.name,
        )

    await client.close()
    try:
        await connect_task
    except Exception:
        pass
    log.info("Готово. Запускайте: .\\scripts\\suggest\\run-telegram-suggest-bot.ps1")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
