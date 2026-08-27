"""Slash command /prohodka: request a temporary role via suggest moderation."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.adapters.discord import keyboards, texts
from bot.adapters.discord.context import DiscordContext
from bot.adapters.discord.moderation import (
    ensure_moderator,
    fetch_message,
    resolve_channel,
)
from bot.core.models import PassRequest, PassRequestStatus, utcnow
from bot.core.pass_service import PassService, format_pass_duration

logger = logging.getLogger(__name__)

PASS_COLORS = {
    PassRequestStatus.pending: 0x5865F2,
    PassRequestStatus.approved: 0x57F287,
    PassRequestStatus.rejected: 0xED4245,
    PassRequestStatus.expired: 0x99AAB5,
}
STATUS_LABELS = {
    PassRequestStatus.pending: "ожидает модерацию",
    PassRequestStatus.approved: "принята",
    PassRequestStatus.rejected: "отклонена",
    PassRequestStatus.expired: "истекла",
}


def _duration_ask_phrase(duration_sec: int) -> str:
    if duration_sec == 0:
        return "без ограничения"
    return f"на {format_pass_duration(duration_sec)}"


def build_pass_embed(
    request: PassRequest, *, label: str, duration_sec: int
) -> discord.Embed:
    user_line = f"<@{request.user_id}> (`{request.username or request.user_id}`)"
    embed = discord.Embed(
        title=f"Заявка на {label}",
        description=(
            f"{user_line} просит **{label}** {_duration_ask_phrase(duration_sec)}."
        ),
        color=PASS_COLORS.get(request.status, 0x99AAB5),
    )
    embed.add_field(name="Автор", value=request.display_name, inline=True)
    embed.add_field(
        name="Статус",
        value=STATUS_LABELS.get(request.status, request.status.value),
        inline=True,
    )
    if request.expires_at is not None:
        embed.add_field(
            name="До",
            value=request.expires_at.strftime("%Y-%m-%d %H:%M UTC"),
            inline=False,
        )
    if request.decided_by and request.status is not PassRequestStatus.pending:
        embed.add_field(
            name="Решение",
            value=f"<@{request.decided_by}>"
            if request.decided_by.isdigit()
            else request.decided_by,
            inline=True,
        )
    embed.set_footer(text=f"Проходка #{request.id}")
    return embed


def member_has_role(member: discord.Member | None, role_id: str | None) -> bool:
    if member is None or not role_id:
        return False
    return any(str(role.id) == str(role_id) for role in member.roles)


class PassCog(commands.Cog, name="pass"):
    """Member command + moderation card + timed role grant."""

    def __init__(
        self, bot: commands.Bot, ctx: DiscordContext, passes: PassService
    ) -> None:
        self.bot = bot
        self.ctx = ctx
        self.passes = passes
        self._expiry_task: asyncio.Task[None] | None = None
        self._expiry_wakeup = asyncio.Event()

    async def cog_load(self) -> None:
        restored = 0
        for request in self.passes.list_pending():
            if request.id is None or not request.mod_message_id:
                continue
            try:
                message_id = int(request.mod_message_id)
            except (TypeError, ValueError):
                continue
            self.bot.add_view(self.build_view(request), message_id=message_id)
            restored += 1
        if restored:
            logger.info("Restored pass cards: %s", restored)
        ru_name = (self.passes.config.command or "проходка").strip() or "проходка"
        self.pass_command.name_localizations = {
            discord.Locale.russian: ru_name
        }
        raw_name = getattr(self.pass_command, "_name", None)
        extras = getattr(raw_name, "extras", None)
        if isinstance(extras, dict):
            extras["ru"] = ru_name
        self._expiry_task = asyncio.create_task(
            self._expiry_loop(), name="pass-expiry"
        )
        logger.info(
            "Pass expiry loop started (idle poll %.0fs)",
            self.passes.config.expiry_poll_sec,
        )

    async def cog_unload(self) -> None:
        task, self._expiry_task = self._expiry_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def build_view(self, request: PassRequest) -> keyboards.PassModerationView:
        request_id = int(request.id or 0)

        async def on_approve(interaction: discord.Interaction) -> None:
            await self.handle_approve(interaction, request_id)

        async def on_reject(interaction: discord.Interaction) -> None:
            await self.handle_reject(interaction, request_id)

        return keyboards.PassModerationView(
            request_id,
            on_approve=on_approve,
            on_reject=on_reject,
            can_decide=request.status is PassRequestStatus.pending,
        )

    @app_commands.command(
        name=app_commands.locale_str("prohodka", ru="проходка"),
        description="Запросить временную роль проходки",
    )
    @app_commands.guild_only()
    async def pass_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            fetched = interaction.guild.get_member(interaction.user.id)
            member = fetched or interaction.user
        already = member_has_role(
            member if isinstance(member, discord.Member) else None,
            self.passes.role_id_for(str(interaction.guild.id)),
        )
        result = self.passes.create_request(
            guild_id=str(interaction.guild.id),
            user_id=str(interaction.user.id),
            display_name=getattr(interaction.user, "display_name", None)
            or interaction.user.name,
            username=interaction.user.name,
            blocked=self.ctx.is_blocked(interaction.user.id),
            already_has_role=already,
        )
        if not result.ok or result.request is None:
            await keyboards.respond(interaction, result.message)
            return
        try:
            posted = await self.post_card(result.request)
        except Exception:  # noqa: BLE001
            logger.exception("Pass card %s failed", result.request.id)
            posted = None
        if posted is None:
            self.passes.abort(int(result.request.id))
            await keyboards.respond(
                interaction,
                "Не удалось отправить заявку в канал модерации. "
                "Напишите админу.",
            )
            return
        await keyboards.respond(interaction, result.message)

    async def handle_approve(
        self, interaction: discord.Interaction, request_id: int
    ) -> None:
        if not await ensure_moderator(interaction, self.ctx):
            return
        current = self.passes.get(request_id)
        if current is None or current.status is not PassRequestStatus.pending:
            await keyboards.respond(
                interaction, "Эту заявку уже разобрали."
            )
            return
        if interaction.guild is None or str(interaction.guild.id) != current.guild_id:
            await keyboards.respond(
                interaction, "Эту заявку нужно разобрать на том же сервере."
            )
            return
        await interaction.response.defer(ephemeral=True)
        decided = self.passes.approve(
            request_id, decided_by=str(interaction.user.id)
        )
        if not decided.ok or decided.request is None:
            await keyboards.respond(interaction, decided.message)
            if decided.request is not None:
                await self.refresh_card(decided.request)
            return
        granted = await self.grant_role(decided.request)
        if not granted:
            reopened = self.passes.reopen(int(decided.request.id))
            if reopened is not None:
                await self.refresh_card(reopened)
            await keyboards.respond(
                interaction,
                "Роль не выдалась: поднимите роль бота выше "
                f"«{self.passes.config.label}» и нажмите «Принять» снова.",
            )
            return
        await self.refresh_card(decided.request)
        self.wake_expiry_loop()
        duration_sec = self.passes.duration_sec_for(decided.request.guild_id)
        if duration_sec == 0:
            notify = (
                f"Заявка принята: «{self.passes.config.label}» "
                "без ограничения."
            )
        else:
            notify = (
                f"Заявка принята: «{self.passes.config.label}» на "
                f"{format_pass_duration(duration_sec)}."
            )
        await self.notify_user(decided.request, notify)
        await keyboards.respond(interaction, decided.message)

    async def handle_reject(
        self, interaction: discord.Interaction, request_id: int
    ) -> None:
        if not await ensure_moderator(interaction, self.ctx):
            return
        current = self.passes.get(request_id)
        if current is not None and (
            interaction.guild is None
            or str(interaction.guild.id) != current.guild_id
        ):
            await keyboards.respond(
                interaction, "Эту заявку нужно разобрать на том же сервере."
            )
            return
        await interaction.response.defer(ephemeral=True)
        decided = self.passes.reject(
            request_id, decided_by=str(interaction.user.id)
        )
        if decided.request is not None:
            await self.refresh_card(decided.request)
        if decided.ok and decided.request is not None:
            await self.notify_user(
                decided.request,
                "Заявка на проходку отклонена. "
                f"Повторно через {format_pass_duration(self.passes.config.reject_cooldown_sec)}.",
            )
        await keyboards.respond(interaction, decided.message)

    async def grant_role(self, request: PassRequest) -> bool:
        role_id = self.passes.role_id_for(request.guild_id)
        if not role_id:
            return False
        guild = self.bot.get_guild(int(request.guild_id))
        if guild is None:
            return False
        role = guild.get_role(int(role_id))
        if role is None:
            logger.warning("Pass role %s not found", role_id)
            return False
        member = await self._fetch_member(guild, request.user_id)
        if member is None:
            logger.warning("Pass member %s not found", request.user_id)
            return False
        if role in member.roles:
            return True
        try:
            await member.add_roles(
                role,
                reason=(
                    f"pass #{request.id} for "
                    f"{format_pass_duration(self.passes.duration_sec_for(request.guild_id))}"
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not grant pass role to %s",
                request.user_id,
            )
            return False
        return True

    async def revoke_role(self, request: PassRequest) -> bool:
        role_id = self.passes.role_id_for(request.guild_id)
        if not role_id:
            return True
        guild = self.bot.get_guild(int(request.guild_id))
        if guild is None:
            logger.warning(
                "Pass %s: guild %s unavailable, revoke deferred",
                request.id,
                request.guild_id,
            )
            return False
        role = guild.get_role(int(role_id))
        if role is None:
            logger.warning("Pass role %s not found, revoke deferred", role_id)
            return False
        member = await self._fetch_member(guild, request.user_id)
        if member is None:
            logger.info(
                "Member %s left — pass #%s has nothing to revoke",
                request.user_id,
                request.id,
            )
            return True
        if role not in member.roles:
            return True
        try:
            await member.remove_roles(
                role, reason=f"pass #{request.id} expired"
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not revoke pass role from %s", request.user_id
            )
            return False
        logger.info("Revoked pass #%s from user %s", request.id, request.user_id)
        return True

    async def _fetch_member(
        self, guild: discord.Guild, user_id: str
    ) -> discord.Member | None:
        try:
            numeric = int(user_id)
        except (TypeError, ValueError):
            return None
        member = guild.get_member(numeric)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(numeric)
        except (discord.NotFound, discord.HTTPException):
            return None

    async def post_card(self, request: PassRequest) -> discord.Message | None:
        channel = await self.resolve_mod_channel(request.guild_id)
        if channel is None:
            logger.warning(
                "Pass %s: moderation channel not found", request.id
            )
            return None
        embed = build_pass_embed(
            request,
            label=self.passes.config.label,
            duration_sec=self.passes.duration_sec_for(request.guild_id),
        )
        view = self.build_view(request)
        try:
            message = await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Could not post pass card %s", request.id)
            return None
        except Exception:  # noqa: BLE001
            logger.exception("Pass card %s: unexpected error", request.id)
            return None
        self.passes.save_mod_ref(
            request,
            channel_id=str(getattr(channel, "id", "")),
            message_id=str(message.id),
        )
        return message

    async def refresh_card(self, request: PassRequest) -> None:
        if not request.mod_channel_id or not request.mod_message_id:
            return
        message = await fetch_message(
            self.bot, request.mod_channel_id, request.mod_message_id
        )
        if message is None:
            return
        try:
            await message.edit(
                embed=build_pass_embed(
                    request,
                    label=self.passes.config.label,
                    duration_sec=self.passes.duration_sec_for(request.guild_id),
                ),
                view=self.build_view(request),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not refresh pass card %s", request.id)

    async def notify_user(self, request: PassRequest, text: str) -> None:
        try:
            user_id = int(request.user_id)
        except (TypeError, ValueError):
            return
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                return
        try:
            await user.send(text)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Pass DM closed for %s", user_id)

    async def resolve_mod_channel(
        self, guild_id: str | None
    ) -> discord.abc.Messageable | None:
        if not guild_id:
            return None
        config = self.ctx.guild_config(guild_id)
        if config is not None and config.mod_channel_id:
            channel = await resolve_channel(self.bot, config.mod_channel_id)
            if channel is not None:
                return channel
        from bot.adapters.discord.guild_decorate import is_mod_channel_name

        try:
            numeric = int(guild_id)
        except (TypeError, ValueError):
            return None
        guild = self.bot.get_guild(numeric)
        if guild is None:
            return None
        for text_channel in guild.text_channels:
            if is_mod_channel_name(text_channel.name):
                self.ctx.services.guilds.set_channels(
                    guild_id,
                    mod_channel_id=str(text_channel.id),
                )
                return text_channel
        return None

    def wake_expiry_loop(self) -> None:
        """Recalculate the next expiry sleep (new grant or failed revoke)."""
        self._expiry_wakeup.set()

    async def _expiry_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.sync_grants()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Pass expiry loop failed")
            delay = self.passes.next_sleep_sec()
            self._expiry_wakeup.clear()
            try:
                await asyncio.wait_for(self._expiry_wakeup.wait(), timeout=delay)
            except (asyncio.TimeoutError, TimeoutError):
                pass

    async def sync_grants(self) -> None:
        now = utcnow()
        for request in self.passes.due_grants():
            if request.id is None:
                continue
            revoked = await self.revoke_role(request)
            if not revoked:
                continue
            expired = self.passes.expire(int(request.id))
            if expired is not None:
                await self.refresh_card(expired)
                await self.notify_user(
                    expired,
                    f"Срок «{self.passes.config.label}» истёк.",
                )
                from bot.adapters.action_log import log_bot_action

                log_bot_action(
                    f"Проходка истекла: user {expired.user_id} "
                    f"«{self.passes.config.label}»",
                    action="pass.expire",
                    actor="bot",
                    submission_id=expired.id,
                )
        for request in self.passes.list_approved():
            if request.expires_at is not None and request.expires_at <= now:
                continue
            await self.grant_role(request)
