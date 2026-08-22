"""Moderation cards in the Discord mod channel.

Buttons call `ModerationService`; publishing to the Telegram channel is left to
the scheduler (Agent D) or to the optional `DiscordContext.publish` hook.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import discord

from bot.adapters.discord import content, keyboards, permissions, texts
from bot.adapters.discord.context import DiscordContext
from bot.core import rules
from bot.core.approve_flow import finalize_approval
from bot.core.models import (
    ContentType,
    Platform,
    PublishTarget,
    Source,
    Submission,
    SubmissionStatus,
    utcnow,
)
from bot.core.services import SubmissionNotFoundError

logger = logging.getLogger(__name__)

CARD_COLORS: dict[SubmissionStatus, int] = {
    SubmissionStatus.pending: 0x5865F2,
    SubmissionStatus.approved: 0x57F287,
    SubmissionStatus.scheduled: 0xFEE75C,
    SubmissionStatus.published: 0x2ECC71,
    SubmissionStatus.rejected: 0xED4245,
}
RESTORE_LIMIT = 200


# --- card rendering ----------------------------------------------------------


def build_card_embed(
    submission: Submission,
    *,
    attachment_image: str | None = None,
) -> discord.Embed:
    """Moderator view of a submission; the real author is always shown.

    ``attachment_image`` — filename of an attached preview file so Discord
    renders it in the embed via ``attachment://`` (same idea as TG photo cards).
    """
    embed = discord.Embed(
        title=texts.card_title(submission),
        description=(submission.text or "—")[: rules.TEXT_LIMIT],
        color=CARD_COLORS.get(submission.status, 0x99AAB5),
    )
    embed.add_field(
        name="Автор", value=texts.author_block(submission), inline=False
    )
    embed.add_field(name="Подпись", value=texts.privacy_line(submission))
    embed.add_field(name="Источник", value=texts.source_note(submission))
    embed.add_field(
        name="Куда публиковать",
        value=texts.PUBLISH_TARGET_LABELS.get(
            submission.publish_target.value, submission.publish_target.value
        ),
    )
    embed.add_field(name="Статус", value=texts.status_line(submission))
    embed.add_field(
        name="Вложения", value=texts.describe_media(submission.media)
    )

    if submission.scheduled_at is not None:
        embed.add_field(
            name="Запланировано",
            value=format_moment(submission.scheduled_at),
            inline=False,
        )
    if submission.reject_reason:
        embed.add_field(
            name="Причина отклонения",
            value=submission.reject_reason,
            inline=False,
        )

    if attachment_image:
        embed.set_image(url=f"attachment://{attachment_image}")
    # Discord does not render cdn.discordapp.com attachment URLs inside
    # embeds (especially after the source message is deleted). Preview
    # must be a re-uploaded `discord.File` + attachment://.

    embed.set_footer(text=f"Заявка №{submission.id}")
    return embed


def preview_filename_from_message(message: discord.Message) -> str | None:
    """Reuse an already-attached preview image after card edits."""
    for att in message.attachments:
        name = (att.filename or "").strip()
        if not name:
            continue
        lower = name.lower()
        if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return name
    return None


def format_moment(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    return moment.strftime("%Y-%m-%d %H:%M UTC")


# --- guards ------------------------------------------------------------------


def is_moderator(interaction: discord.Interaction, ctx: DiscordContext) -> bool:
    user = interaction.user
    if user is None:
        return False
    return permissions.member_can_moderate(
        user,
        ctx.guild_config(interaction.guild_id),
        is_platform_admin=ctx.is_platform_admin(user.id),
    )


async def ensure_moderator(
    interaction: discord.Interaction, ctx: DiscordContext
) -> bool:
    if is_moderator(interaction, ctx):
        return True
    await keyboards.respond(interaction, texts.NOT_MODERATOR)
    return False


# --- views -------------------------------------------------------------------


def build_moderation_view(
    bot: discord.Client, ctx: DiscordContext, submission: Submission
) -> keyboards.ModerationView:
    submission_id = int(submission.id or 0)

    async def on_approve(interaction: discord.Interaction) -> None:
        await handle_approve(bot, ctx, submission_id, interaction)

    async def on_reject(interaction: discord.Interaction) -> None:
        await handle_reject(bot, ctx, submission_id, interaction)

    async def on_reply(interaction: discord.Interaction) -> None:
        await handle_reply(bot, ctx, submission_id, interaction)

    async def on_schedule(interaction: discord.Interaction) -> None:
        await handle_schedule(bot, ctx, submission_id, interaction)

    async def on_edit(interaction: discord.Interaction) -> None:
        await handle_edit_text(bot, ctx, submission_id, interaction)

    async def on_target_tg(interaction: discord.Interaction) -> None:
        await handle_set_target(
            bot, ctx, submission_id, interaction, PublishTarget.telegram
        )

    async def on_target_ds(interaction: discord.Interaction) -> None:
        await handle_set_target(
            bot, ctx, submission_id, interaction, PublishTarget.discord
        )

    async def on_target_both(interaction: discord.Interaction) -> None:
        await handle_set_target(
            bot, ctx, submission_id, interaction, PublishTarget.both
        )

    can_edit = submission.status in (
        SubmissionStatus.pending,
        SubmissionStatus.scheduled,
        SubmissionStatus.approved,
    )
    return keyboards.ModerationView(
        submission_id,
        on_approve=on_approve,
        on_reject=on_reject,
        on_reply=on_reply,
        on_schedule=on_schedule,
        on_edit=on_edit,
        on_target_tg=on_target_tg,
        on_target_ds=on_target_ds,
        on_target_both=on_target_both,
        can_decide=not rules.is_handled(submission.status),
        can_schedule=not rules.is_terminal(submission.status),
        can_edit=can_edit,
        publish_target=submission.publish_target.value,
    )


async def handle_set_target(
    bot: discord.Client,
    ctx: DiscordContext,
    submission_id: int,
    interaction: discord.Interaction,
    target: PublishTarget,
) -> None:
    if not await ensure_moderator(interaction, ctx):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        updated = await ctx.services.submissions.set_publish_target(
            submission_id, target
        )
    except (SubmissionNotFoundError, ValueError) as exc:
        await keyboards.respond(interaction, str(exc))
        return
    # Refresh the card so the highlight moves.
    if interaction.message is not None:
        try:
            attachment_image = preview_filename_from_message(interaction.message)
            await interaction.message.edit(
                embed=build_card_embed(
                    updated, attachment_image=attachment_image
                ),
                view=build_moderation_view(bot, ctx, updated),
            )
        except discord.HTTPException:
            logger.debug("Не удалось обновить карточку после смены назначения")
    await keyboards.respond(
        interaction,
        "Назначение: "
        + texts.PUBLISH_TARGET_LABELS.get(target.value, target.value),
    )


async def handle_edit_text(
    bot: discord.Client,
    ctx: DiscordContext,
    submission_id: int,
    interaction: discord.Interaction,
) -> None:
    if not is_moderator(interaction, ctx):
        await keyboards.respond(interaction, texts.NOT_MODERATOR)
        return
    current = ctx.services.submissions.get(submission_id)
    if current is None:
        await keyboards.respond(interaction, texts.NOT_FOUND)
        return
    if current.status not in (
        SubmissionStatus.pending,
        SubmissionStatus.scheduled,
        SubmissionStatus.approved,
    ):
        await keyboards.respond(interaction, texts.EDIT_FORBIDDEN)
        return

    async def apply_text(
        modal_interaction: discord.Interaction, value: str
    ) -> None:
        await modal_interaction.response.defer(ephemeral=True)
        try:
            updated = await ctx.services.submissions.edit_moderator_text(
                submission_id, value
            )
        except ValueError as exc:
            await keyboards.respond(modal_interaction, str(exc)[:190])
            return
        except SubmissionNotFoundError:
            await keyboards.respond(modal_interaction, texts.NOT_FOUND)
            return
        await update_moderation_cards(bot, ctx, updated)
        await keyboards.respond(modal_interaction, texts.EDIT_SAVED)

    await interaction.response.send_modal(
        keyboards.edit_text_modal(
            submission_id, current=current.text, handler=apply_text
        )
    )


# --- button handlers ---------------------------------------------------------


async def handle_approve(
    bot: discord.Client,
    ctx: DiscordContext,
    submission_id: int,
    interaction: discord.Interaction,
) -> None:
    if not await ensure_moderator(interaction, ctx):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        handled = await approve_submission(
            ctx, submission_id, moderator_id=str(interaction.user.id)
        )
    except (SubmissionNotFoundError, ValueError) as exc:
        await keyboards.respond(interaction, str(exc))
        return
    except Exception:  # noqa: BLE001 - publishing lives in another adapter
        logger.exception("Публикация заявки %s не удалась", submission_id)
        await keyboards.respond(interaction, texts.PUBLISH_FAILED)
        return
    await keyboards.respond(
        interaction,
        texts.ALREADY_HANDLED
        if handled
        else texts.approved_note(submission_id),
    )


async def approve_submission(
    ctx: DiscordContext, submission_id: int, *, moderator_id: str | None
) -> bool:
    """Approve and hand the post over; returns True if already handled.

    With an injected publish hook the shared core flow publishes right away and
    marks the submission published. Without a hook the post is due immediately,
    so Agent D's scheduler picks it up on the next tick — the Discord adapter
    never talks to the Telegram channel itself.
    """
    if ctx.publish is not None:
        current = ctx.services.submissions.get(submission_id)
        outcome = await finalize_approval(
            ctx.services.moderation,
            submission_id=submission_id,
            with_author=not bool(current and current.want_anonymous),
            publish_at=None,
            publish_now_cb=ctx.publish,
            submissions=ctx.services.submissions,
            moderator_platform=Platform.discord,
            moderator_id=moderator_id,
        )
        return outcome.already_handled

    result = await ctx.services.moderation.approve(
        submission_id,
        moderator_platform=Platform.discord,
        moderator_id=moderator_id,
        scheduled_at=utcnow(),
    )
    return result.already_handled


async def handle_reject(
    bot: discord.Client,
    ctx: DiscordContext,
    submission_id: int,
    interaction: discord.Interaction,
) -> None:
    if not is_moderator(interaction, ctx):
        await keyboards.respond(interaction, texts.NOT_MODERATOR)
        return

    async def submit_reason(
        modal_interaction: discord.Interaction, reason: str
    ) -> None:
        await modal_interaction.response.defer(ephemeral=True)
        try:
            result = await ctx.services.moderation.reject(
                submission_id,
                reason=reason or None,
                moderator_platform=Platform.discord,
                moderator_id=str(modal_interaction.user.id),
            )
        except (SubmissionNotFoundError, ValueError) as exc:
            await keyboards.respond(modal_interaction, str(exc))
            return
        if result.already_handled:
            await keyboards.respond(modal_interaction, texts.ALREADY_HANDLED)
            return
        await keyboards.respond(
            modal_interaction,
            texts.rejected_note(submission_id, reason or None),
        )

    await interaction.response.send_modal(
        keyboards.reject_reason_modal(submission_id, handler=submit_reason)
    )


async def handle_schedule(
    bot: discord.Client,
    ctx: DiscordContext,
    submission_id: int,
    interaction: discord.Interaction,
) -> None:
    if not is_moderator(interaction, ctx):
        await keyboards.respond(interaction, texts.NOT_MODERATOR)
        return

    async def submit_moment(
        modal_interaction: discord.Interaction, value: str
    ) -> None:
        moment = content.parse_schedule_input(value)
        if moment is None:
            await keyboards.respond(
                modal_interaction, texts.SCHEDULE_BAD_FORMAT
            )
            return
        if moment <= discord.utils.utcnow():
            await keyboards.respond(modal_interaction, texts.SCHEDULE_IN_PAST)
            return
        await modal_interaction.response.defer(ephemeral=True)
        try:
            result = await ctx.services.moderation.schedule(
                submission_id, moment
            )
        except (SubmissionNotFoundError, ValueError) as exc:
            await keyboards.respond(modal_interaction, str(exc))
            return
        if result.already_handled:
            await keyboards.respond(modal_interaction, texts.ALREADY_HANDLED)
            return
        await keyboards.respond(
            modal_interaction,
            texts.scheduled_note(submission_id, format_moment(moment)),
        )

    await interaction.response.send_modal(
        keyboards.schedule_modal(submission_id, handler=submit_moment)
    )


async def handle_reply(
    bot: discord.Client,
    ctx: DiscordContext,
    submission_id: int,
    interaction: discord.Interaction,
) -> None:
    if not is_moderator(interaction, ctx):
        await keyboards.respond(interaction, texts.NOT_MODERATOR)
        return

    async def submit_reply(
        modal_interaction: discord.Interaction, text: str
    ) -> None:
        await modal_interaction.response.defer(ephemeral=True)
        submission = ctx.services.submissions.get(submission_id)
        if submission is None:
            await keyboards.respond(
                modal_interaction, f"Заявка {submission_id} не найдена"
            )
            return
        message = texts.moderator_reply(text)
        if submission.source is Source.discord:
            delivered = await send_author_dm(bot, submission, message)
        elif ctx.notify_telegram_author is not None:
            delivered = await call_notify_hook(ctx, submission, message)
        else:
            await keyboards.respond(
                modal_interaction, texts.REPLY_CROSS_PLATFORM
            )
            return
        await keyboards.respond(
            modal_interaction,
            texts.REPLY_SENT if delivered else texts.REPLY_FAILED,
        )

    await interaction.response.send_modal(
        keyboards.reply_modal(submission_id, handler=submit_reply)
    )


async def call_notify_hook(
    ctx: DiscordContext, submission: Submission, message: str
) -> bool:
    if ctx.notify_telegram_author is None:
        return False
    try:
        return bool(await ctx.notify_telegram_author(submission, message))
    except Exception:  # noqa: BLE001 - hook belongs to another adapter
        logger.exception(
            "Notify hook failed for submission %s", submission.id
        )
        return False


# --- card lifecycle ----------------------------------------------------------


async def resolve_channel(
    bot: discord.Client, channel_id: str | int | None
) -> discord.abc.Messageable | None:
    if channel_id in (None, ""):
        return None
    try:
        numeric = int(channel_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    channel = bot.get_channel(numeric)
    if channel is None:
        try:
            channel = await bot.fetch_channel(numeric)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning("Канал %s недоступен", channel_id)
            return None
    if isinstance(channel, discord.abc.Messageable):
        return channel
    return None


async def _download_url_bytes(url: str, *, timeout_sec: float = 30.0) -> bytes | None:
    """Fetch Discord CDN (or any) attachment bytes for moderation preview."""
    import aiohttp

    try:
        from bot.core.media_store import DOWNLOAD_HEADERS

        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(
            timeout=timeout, headers=DOWNLOAD_HEADERS
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.read()
        # Discord message attachment limit is well above typical photos;
        # keep a hard cap so a huge file cannot break the mod channel.
        if len(data) > 8 * 1024 * 1024:
            logger.warning("Превью слишком большое (%s bytes), пропускаю", len(data))
            return None
        return data
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось скачать URL для превью модерации")
        return None


def _preview_suffix(content_type: ContentType) -> str:
    return {
        ContentType.photo: "jpg",
        ContentType.video: "mp4",
        ContentType.sticker: "webp",
    }.get(content_type, "bin")


async def _build_preview_files(
    ctx: DiscordContext,
    submission: Submission,
    *,
    bot: discord.Client | None = None,
    limit: int = 4,
) -> tuple[list[discord.File], str | None]:
    """Copy submission media into discord.File for the mod channel.

    Telegram ``file_id`` and local cache are re-uploaded so admins see the
    photo next to the card. Discord CDN URLs are fetched with the bot HTTP
    client when possible; they are never used as embed.image (Discord
    will not render them).
    Returns ``(files, embed_image_filename)`` for ``attachment://``.
    """
    from io import BytesIO

    tg = ctx.telegram_bot
    files: list[discord.File] = []
    embed_image: str | None = None
    for item in sorted(submission.media, key=lambda m: m.order_index):
        if len(files) >= limit:
            break
        if item.content_type not in (
            ContentType.photo,
            ContentType.video,
            ContentType.sticker,
        ):
            continue
        suffix = _preview_suffix(item.content_type)
        filename = f"preview_{item.order_index}.{suffix}"
        data: bytes | None = None
        if item.local_path:
            from pathlib import Path

            path = Path(item.local_path)
            if path.is_file():
                data = path.read_bytes()
            else:
                logger.warning(
                    "local_path для превью заявки %s не найден: %s",
                    submission.id,
                    item.local_path,
                )
        if data is None and item.file_id and tg is not None:
            try:
                file = await tg.get_file(item.file_id)
                buf = BytesIO()
                await tg.download_file(file.file_path, destination=buf)
                data = buf.getvalue()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Не удалось скачать TG media для превью заявки %s",
                    submission.id,
                )
        if data is None and item.discord_attachment_url:
            http = getattr(bot, "http", None) if bot is not None else None
            if http is not None:
                try:
                    data = await http.get_from_cdn(item.discord_attachment_url)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "get_from_cdn не удалось для заявки %s",
                        submission.id,
                        exc_info=True,
                    )
                    data = None
            if data is None:
                data = await _download_url_bytes(item.discord_attachment_url)
        if not data:
            continue
        files.append(discord.File(BytesIO(data), filename=filename))
        if (
            embed_image is None
            and item.content_type in (ContentType.photo, ContentType.sticker)
        ):
            embed_image = filename
    return files, embed_image


async def _send_moderation_message(
    channel: discord.abc.Messageable,
    bot: discord.Client,
    ctx: DiscordContext,
    submission: Submission,
) -> discord.Message:
    files, embed_image = await _build_preview_files(ctx, submission, bot=bot)
    embed = build_card_embed(submission, attachment_image=embed_image)
    view = build_moderation_view(bot, ctx, submission)
    kwargs: dict[str, Any] = {"embed": embed, "view": view}
    if files:
        kwargs["files"] = files
    try:
        return await channel.send(**kwargs)
    except discord.HTTPException:
        if not files:
            raise
        logger.exception(
            "Карточка заявки %s с превью не ушла, шлю без файла",
            submission.id,
        )
        kwargs.pop("files", None)
        kwargs["embed"] = build_card_embed(submission)
        return await channel.send(**kwargs)


async def post_moderation_card(
    bot: discord.Client, ctx: DiscordContext, submission: Submission
) -> discord.Message | None:
    if submission.id is None:
        return None
    fresh = ctx.services.submissions.get(int(submission.id))
    if fresh is not None:
        submission = fresh
    config = ctx.guild_config(submission.guild_id)
    if config is None or not config.mod_channel_id:
        for candidate in ctx.services.guilds.list_all():
            if candidate.mod_channel_id:
                config = candidate
                break
    channel: discord.abc.Messageable | None = None
    guild_id_for_persist: str | None = None
    if config is not None and config.mod_channel_id:
        channel = await resolve_channel(bot, config.mod_channel_id)
        guild_id_for_persist = config.guild_id
    if channel is None:
        from bot.adapters.discord.guild_decorate import is_mod_channel_name

        for guild in bot.guilds:
            for text_channel in guild.text_channels:
                if is_mod_channel_name(text_channel.name):
                    channel = text_channel
                    guild_id_for_persist = str(guild.id)
                    ctx.services.guilds.set_channels(
                        str(guild.id),
                        mod_channel_id=str(text_channel.id),
                    )
                    break
            if channel is not None:
                break
    if channel is None:
        logger.warning(
            "Заявка %s: канал модерации не настроен (guild %s)",
            submission.id,
            submission.guild_id,
        )
        return None
    try:
        message = await _send_moderation_message(channel, bot, ctx, submission)
    except (discord.Forbidden, discord.HTTPException):
        logger.exception(
            "Не удалось отправить карточку заявки %s", submission.id
        )
        return None
    target_id = str(getattr(channel, "id", ""))
    ctx.services.moderation.save_moderation_ref(
        int(submission.id),
        platform=Platform.discord,
        target_id=target_id,
        message_id=str(message.id),
    )
    _ = guild_id_for_persist
    return message



async def update_moderation_cards(
    bot: discord.Client, ctx: DiscordContext, submission: Submission
) -> None:
    if submission.id is None:
        return
    refs = ctx.services.moderation.get_moderation_refs(
        int(submission.id), platform=Platform.discord
    )
    for ref in refs:
        message = await fetch_message(bot, ref.target_id, ref.message_id)
        if message is None:
            continue
        attachment_image = preview_filename_from_message(message)
        embed = build_card_embed(
            submission, attachment_image=attachment_image
        )
        try:
            await message.edit(
                embed=embed,
                view=build_moderation_view(bot, ctx, submission),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Не удалось обновить карточку заявки %s", submission.id
            )


async def restore_moderation_views(
    bot: discord.Client, ctx: DiscordContext
) -> int:
    """Re-attach persistent views to cards that survived a restart."""
    restored = 0
    for status in (
        SubmissionStatus.pending,
        SubmissionStatus.approved,
        SubmissionStatus.scheduled,
    ):
        for submission in ctx.services.submissions.list_by_status(
            status, limit=RESTORE_LIMIT
        ):
            if submission.id is None:
                continue
            for ref in ctx.services.moderation.get_moderation_refs(
                int(submission.id), platform=Platform.discord
            ):
                try:
                    message_id = int(ref.message_id)
                except (TypeError, ValueError):
                    continue
                bot.add_view(
                    build_moderation_view(bot, ctx, submission),
                    message_id=message_id,
                )
                restored += 1
    return restored


# --- shared message helpers --------------------------------------------------


async def fetch_message(
    bot: discord.Client, channel_id: str | int | None, message_id: str | int
) -> discord.Message | None:
    channel = await resolve_channel(bot, channel_id)
    if channel is None:
        return None
    try:
        return await channel.fetch_message(int(message_id))
    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
        TypeError,
        ValueError,
    ):
        return None


async def send_author_dm(
    bot: discord.Client, submission: Submission, text: str
) -> bool:
    if submission.source is not Source.discord:
        return False
    try:
        user_id = int(submission.author_platform_user_id)
    except (TypeError, ValueError):
        return False
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except (discord.NotFound, discord.HTTPException):
            return False
    try:
        await user.send(text)
    except (discord.Forbidden, discord.HTTPException):
        return False
    return True


async def set_status_reaction(
    bot: discord.Client, submission: Submission
) -> None:
    """Mirror the submission status as an emoji on the original message."""
    if submission.source is not Source.discord:
        return
    if not (submission.source_chat_id and submission.source_message_id):
        return
    emoji = texts.STATUS_EMOJI.get(submission.status)
    if emoji is None:
        return
    message = await fetch_message(
        bot, submission.source_chat_id, submission.source_message_id
    )
    if message is None:
        return
    me = bot.user
    for reaction in message.reactions:
        current = str(reaction.emoji)
        if current == emoji or current not in texts.STATUS_EMOJI.values():
            continue
        try:
            await message.remove_reaction(reaction.emoji, me)  # type: ignore[arg-type]
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass
    try:
        await message.add_reaction(emoji)
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        logger.debug(
            "Не удалось поставить реакцию на сообщение %s",
            submission.source_message_id,
        )
