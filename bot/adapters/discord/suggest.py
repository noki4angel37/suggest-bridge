"""Collecting submissions in Discord: suggest channel listener and /suggest."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.adapters.discord import content, keyboards, permissions, texts
from bot.adapters.discord.context import DiscordContext
from bot.adapters.discord.moderation import CARD_COLORS
from bot.core.models import (
    ContentType,
    GuildConfig,
    MediaItem,
    Platform,
    RefKind,
    Source,
    Submission,
)
from bot.core.rules import TEXT_LIMIT
from bot.core.services import SubmissionNotFoundError

logger = logging.getLogger(__name__)

CHANNEL_HINT_TTL_SEC = 30.0
DRAFT_COLOR = 0x99AAB5


def _is_named_suggest_channel(name: str) -> bool:
    from bot.adapters.discord.guild_decorate import is_suggest_channel_name

    return is_suggest_channel_name(name)


def attachment_infos(
    message_attachments: list[discord.Attachment],
) -> list[content.AttachmentInfo]:
    infos: list[content.AttachmentInfo] = []
    for attachment in message_attachments:
        url = content.attachment_url(
            getattr(attachment, "url", None),
            getattr(attachment, "proxy_url", None),
        )
        infos.append(
            content.AttachmentInfo(
                url=url,
                content_type=attachment.content_type,
                filename=attachment.filename,
            )
        )
    return infos


def embed_image_infos(message: discord.Message) -> list[content.AttachmentInfo]:
    """Images that arrived as embeds (paste / some mobile clients), not files."""
    infos: list[content.AttachmentInfo] = []
    for embed in message.embeds or ():
        for candidate in (embed.image, embed.thumbnail):
            if candidate is None:
                continue
            url = content.attachment_url(
                getattr(candidate, "url", None),
                getattr(candidate, "proxy_url", None),
            )
            if url:
                infos.append(
                    content.AttachmentInfo(
                        url=url, content_type="image/png", filename="embed.png"
                    )
                )
                break
    return infos


def build_media(
    attachments: list[discord.Attachment],
    stickers: list[discord.StickerItem] | None = None,
    *,
    extra: list[content.AttachmentInfo] | None = None,
) -> list[MediaItem]:
    sticker_urls = [
        sticker.url for sticker in (stickers or ()) if getattr(sticker, "url", None)
    ]
    infos = attachment_infos(attachments)
    if extra:
        infos.extend(extra)
    return content.build_media_items(infos, sticker_urls)


async def read_attachment_bytes(
    attachment: discord.Attachment,
) -> bytes | None:
    """Fetch bytes with the bot token; raw CDN GET often returns 403."""
    try:
        return await attachment.read()
    except (discord.HTTPException, discord.NotFound, OSError) as exc:
        logger.warning(
            "Не удалось прочитать вложение %s: %s", attachment.filename, exc
        )
        return None


def profile_url(user_id: int | str) -> str:
    return f"https://discord.com/users/{user_id}"


def build_draft_embed(submission: Submission) -> discord.Embed:
    embed = discord.Embed(
        title=f"{texts.DRAFT_TITLE} №{submission.id}",
        description=(submission.text or "—")[:TEXT_LIMIT],
        color=CARD_COLORS.get(submission.status, DRAFT_COLOR),
    )
    embed.add_field(name="Подпись", value=texts.privacy_line(submission))
    embed.add_field(
        name="Вложения", value=texts.describe_media(submission.media)
    )
    preview = next(
        (
            item.discord_attachment_url
            for item in submission.media
            if item.content_type
            in (ContentType.photo, ContentType.sticker)
            and item.discord_attachment_url
        ),
        None,
    )
    if preview:
        embed.set_image(url=preview)
    else:
        local = next(
            (
                item.local_path
                for item in submission.media
                if item.content_type
                in (ContentType.photo, ContentType.sticker)
                and item.local_path
            ),
            None,
        )
        if local:
            from pathlib import Path

            embed.set_image(url=f"attachment://{Path(local).name}")
    embed.set_footer(text=texts.DRAFT_HINT)
    return embed


def draft_preview_files(submission: Submission) -> list[discord.File]:
    """Attach local cached images so the draft embed preview still works."""
    from pathlib import Path

    files: list[discord.File] = []
    for item in submission.media:
        if item.content_type not in (ContentType.photo, ContentType.sticker):
            continue
        if not item.local_path:
            continue
        path = Path(item.local_path)
        if path.is_file():
            files.append(discord.File(path, filename=path.name))
            break
    return files


class SuggestCog(commands.Cog, name="suggest"):
    """Draft creation from the suggest channel and from the slash command."""

    def __init__(self, bot: commands.Bot, ctx: DiscordContext) -> None:
        self.bot = bot
        self.ctx = ctx

    # --- guards --------------------------------------------------------------

    def refusal_reason(
        self, member: discord.abc.User, config: GuildConfig | None
    ) -> str | None:
        """Blacklist, propose roles and antiflood; the last one consumes a hit."""
        if self.ctx.is_blocked(member.id):
            return texts.BLOCKED
        if not permissions.member_can_propose(
            member,
            config,
            is_platform_admin=self.ctx.is_platform_admin(member.id),
        ):
            return texts.NO_PROPOSE_ROLE
        decision = self.ctx.services.antiflood.decide_for_guild(
            Platform.discord, str(member.id), guild_config=config
        )
        if not decision.allowed:
            return texts.antiflood(decision.limit, decision.window_sec)
        return None

    # --- draft views ---------------------------------------------------------

    def build_draft_view(self, submission: Submission) -> keyboards.DraftView:
        submission_id = int(submission.id or 0)
        author_id: int | None
        try:
            author_id = int(submission.author_platform_user_id)
        except (TypeError, ValueError):
            author_id = None

        async def on_privacy(
            interaction: discord.Interaction, anonymous: bool
        ) -> None:
            try:
                updated = await self.ctx.services.submissions.set_privacy(
                    submission_id, want_anonymous=anonymous
                )
            except SubmissionNotFoundError:
                await keyboards.respond(interaction, texts.DRAFT_EXPIRED)
                return
            await interaction.response.edit_message(
                embed=build_draft_embed(updated),
                view=self.build_draft_view(updated),
            )

        async def on_edit(interaction: discord.Interaction) -> None:
            current = self.ctx.services.submissions.get(submission_id)
            if current is None:
                await keyboards.respond(interaction, texts.DRAFT_EXPIRED)
                return

            async def apply_text(
                modal_interaction: discord.Interaction, value: str
            ) -> None:
                try:
                    updated = await self.ctx.services.submissions.update_draft(
                        submission_id, text=value
                    )
                except ValueError:
                    await keyboards.respond(
                        modal_interaction, texts.text_too_long(len(value))
                    )
                    return
                except SubmissionNotFoundError:
                    await keyboards.respond(
                        modal_interaction, texts.DRAFT_EXPIRED
                    )
                    return
                await modal_interaction.response.edit_message(
                    embed=build_draft_embed(updated),
                    view=self.build_draft_view(updated),
                )

            await interaction.response.send_modal(
                keyboards.edit_text_modal(
                    submission_id,
                    current=current.text,
                    handler=apply_text,
                )
            )

        async def on_submit(interaction: discord.Interaction) -> None:
            await self.submit_draft(interaction, submission_id)

        async def on_cancel(interaction: discord.Interaction) -> None:
            try:
                await self.ctx.services.submissions.cancel_draft(submission_id)
            except (SubmissionNotFoundError, ValueError):
                await keyboards.respond(interaction, texts.DRAFT_EXPIRED)
                return
            try:
                await interaction.response.edit_message(
                    content=texts.DRAFT_CANCELLED,
                    embed=None,
                    view=None,
                    attachments=[],
                )
            except discord.HTTPException:
                await keyboards.respond(interaction, texts.DRAFT_CANCELLED)

        return keyboards.DraftView(
            submission_id,
            on_privacy=on_privacy,
            on_submit=on_submit,
            on_edit=on_edit,
            on_cancel=on_cancel,
            want_anonymous=submission.want_anonymous,
            author_id=author_id,
        )

    async def submit_draft(
        self, interaction: discord.Interaction, submission_id: int
    ) -> None:
        current = self.ctx.services.submissions.get(submission_id)
        if current is None:
            await keyboards.respond(interaction, texts.DRAFT_EXPIRED)
            return
        if current.want_anonymous is None:
            await keyboards.respond(interaction, texts.NEED_PRIVACY_CHOICE)
            return
        if not content.has_content(current.text, current.media):
            await keyboards.respond(interaction, texts.DRAFT_EMPTY)
            return

        # Moderation cards and the status reaction are posted by event_sync
        # while `submit` runs, so acknowledge the click first.
        await interaction.response.defer()
        try:
            submitted = await self.ctx.services.submissions.submit(
                submission_id
            )
        except (SubmissionNotFoundError, ValueError) as exc:
            await keyboards.respond(interaction, str(exc))
            return

        try:
            await interaction.edit_original_response(
                embed=build_draft_embed(submitted), view=None
            )
        except discord.HTTPException:
            logger.debug("Черновик %s уже недоступен", submission_id)
        await keyboards.respond(interaction, texts.queued(submission_id))

    # --- suggest channel -----------------------------------------------------

    def is_suggest_channel(
        self, message: discord.Message, config: GuildConfig | None
    ) -> bool:
        """Match configured id, or legacy/new suggest channel names.

        Posts in a thread under the suggest channel count as submissions too.
        """
        if message.guild is None:
            return False
        channel = message.channel
        if isinstance(channel, discord.Thread):
            channel = channel.parent
        if not isinstance(channel, discord.TextChannel):
            return False
        if config and config.suggest_channel_id:
            if str(channel.id) == str(config.suggest_channel_id):
                return True
        return _is_named_suggest_channel(channel.name)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if message.webhook_id is not None:
            return
        config = self.ctx.guild_config(message.guild.id)
        if not self.is_suggest_channel(message, config):
            return
        suggest_channel = message.channel
        if isinstance(suggest_channel, discord.Thread):
            suggest_channel = suggest_channel.parent
        # Persist id if we matched by name only (rename / setup drift).
        if (
            config is not None
            and isinstance(suggest_channel, discord.TextChannel)
            and (
                not config.suggest_channel_id
                or str(config.suggest_channel_id) != str(suggest_channel.id)
            )
        ):
            self.ctx.services.guilds.set_channels(
                str(message.guild.id),
                suggest_channel_id=str(suggest_channel.id),
            )
            config = self.ctx.guild_config(message.guild.id)
        await self.handle_channel_message(
            message, config or GuildConfig(guild_id=str(message.guild.id))
        )

    async def collect_attachment_blobs(
        self, message: discord.Message, media: list[MediaItem]
    ) -> dict[int, bytes]:
        """Read bytes while the original message still exists.

        Prefer ``Attachment.read()`` (bot token). Remaining CDN URLs
        (embeds, stickers) are fetched with a Discord bot User-Agent
        *before* the source message is deleted — a later anonymous GET
        usually gets 403.
        """
        from bot.core.media_store import fetch_url_bytes

        blobs: dict[int, bytes] = {}
        usable = [
            attachment
            for attachment in message.attachments
            if content.attachment_url(
                getattr(attachment, "url", None),
                getattr(attachment, "proxy_url", None),
            )
        ]
        media_from_files = [
            item
            for item in media
            if item.discord_attachment_url
            and item.content_type is not ContentType.sticker
        ]
        # Embed extras sit after real files in `media`; pair only attachments.
        for item, attachment in zip(media_from_files, usable):
            data = await read_attachment_bytes(attachment)
            if data:
                blobs[item.order_index] = data

        for item in media:
            if item.order_index in blobs or not item.discord_attachment_url:
                continue
            data = await fetch_url_bytes(item.discord_attachment_url)
            if data:
                blobs[item.order_index] = data
        if message.attachments and not blobs:
            logger.warning(
                "Вложения есть (%s), но байты не прочитаны — превью будет пустым",
                len(message.attachments),
            )
        return blobs

    async def handle_channel_message(
        self, message: discord.Message, config: GuildConfig
    ) -> None:
        """Hide the public message and queue it for moderation immediately.

        No author DM / privacy confirmation step: the card goes to
        `#модерация-предложки` and Telegram admins via EventBus.
        Default signature is anonymous.

        Attachments are read **before** delete: Discord CDN URLs die quickly
        once the source message is gone, and a raw HTTP GET often gets 403.
        """
        extra = embed_image_infos(message)
        media = build_media(
            list(message.attachments), list(message.stickers), extra=extra
        )
        text = message.content or ""
        blobs = await self.collect_attachment_blobs(message, media)

        async def hide() -> None:
            hidden = await self.hide_user_message(message)
            if not hidden:
                await self.reply_hint(
                    message,
                    "Не удалось скрыть сообщение — боту нужно право "
                    "«Управлять сообщениями» в этом канале.",
                )

        if not content.has_content(text, media):
            await hide()
            return

        refusal = self.refusal_reason(message.author, config)
        if refusal:
            await hide()
            await self.reply_hint(
                message, texts.CHANNEL_HINT_DELETED.format(reason=refusal)
            )
            return
        if len(text.strip()) > TEXT_LIMIT:
            await hide()
            await self.reply_hint(
                message,
                texts.CHANNEL_HINT_DELETED.format(
                    reason=texts.text_too_long(len(text.strip()))
                ),
            )
            return

        # Cache while the source message still exists: signed CDN URLs
        # and Attachment.read() both die after delete (404/403).
        draft = await self.create_draft(
            user=message.author,
            guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,
            message_id=None,
            text=text,
            media=media,
            blobs=blobs,
        )
        await hide()
        if draft is None or draft.id is None:
            await self.reply_hint(message, texts.SUBMIT_FAILED)
            return

        try:
            await self.ctx.services.submissions.set_privacy(
                int(draft.id), want_anonymous=True
            )
            submitted = await self.ctx.services.submissions.submit(int(draft.id))
        except (SubmissionNotFoundError, ValueError) as exc:
            logger.warning("Не удалось отправить заявку из канала: %s", exc)
            await self.reply_hint(message, texts.SUBMIT_FAILED)
            return

        await self.reply_hint(
            message, texts.queued(int(submitted.id or draft.id))
        )

    async def hide_user_message(self, message: discord.Message) -> bool:
        """Delete the subscriber message so others cannot read it."""
        try:
            await message.delete()
            return True
        except discord.NotFound:
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning(
                "Не удалось удалить сообщение предложки %s: %s "
                "(нужно право Manage Messages у бота)",
                message.id,
                exc,
            )
            return False

    async def deliver_private_draft(
        self,
        user: discord.abc.User,
        draft: Submission,
        view: keyboards.DraftView,
    ) -> bool:
        """Send draft embed+buttons to the author's DM."""
        embed = build_draft_embed(draft)
        embed.description = (
            f"{texts.DRAFT_DM_NOTE}\n\n{embed.description or '—'}"
        )
        try:
            dm = await user.create_dm()
            view.message = await dm.send(embed=embed, view=view)
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.info(
                "ЛС закрыты у %s — черновик %s не доставлен",
                user.id,
                draft.id,
            )
            return False

    async def dm_or_hint(self, message: discord.Message, text: str) -> None:
        """Prefer DM; fall back to a short auto-deleting channel hint."""
        try:
            await message.author.send(text)
            return
        except (discord.Forbidden, discord.HTTPException):
            pass
        await self.reply_hint(message, text)

    async def reply_hint(self, message: discord.Message, text: str) -> None:
        try:
            channel = message.channel
            await channel.send(text, delete_after=CHANNEL_HINT_TTL_SEC)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Не удалось ответить в канале предложки")

    # --- slash command -------------------------------------------------------

    @app_commands.command(
        name="suggest", description="Отправить заявку в предложку"
    )
    @app_commands.guild_only()
    @app_commands.rename(
        text="текст",
        attachment="вложение",
        attachment2="вложение2",
        attachment3="вложение3",
    )
    @app_commands.describe(
        text=f"Текст заявки, до {TEXT_LIMIT} символов",
        attachment="Фото, видео или файл",
        attachment2="Ещё одно вложение",
        attachment3="Ещё одно вложение",
    )
    async def suggest_command(
        self,
        interaction: discord.Interaction,
        text: str | None = None,
        attachment: discord.Attachment | None = None,
        attachment2: discord.Attachment | None = None,
        attachment3: discord.Attachment | None = None,
    ) -> None:
        if interaction.guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        await interaction.response.defer(ephemeral=True)

        config = self.ctx.guild_config(interaction.guild.id)
        refusal = self.refusal_reason(interaction.user, config)
        if refusal:
            await keyboards.respond(interaction, refusal)
            return

        body = (text or "").strip()
        if len(body) > TEXT_LIMIT:
            await keyboards.respond(interaction, texts.text_too_long(len(body)))
            return

        media = build_media(
            [
                item
                for item in (attachment, attachment2, attachment3)
                if item is not None
            ]
        )
        if not content.has_content(body, media):
            await keyboards.respond(interaction, texts.DRAFT_EMPTY)
            return

        draft = await self.create_draft(
            user=interaction.user,
            guild_id=interaction.guild.id,
            channel_id=(
                interaction.channel.id if interaction.channel else None
            ),
            message_id=None,
            text=body,
            media=media,
            attachments=[
                item
                for item in (attachment, attachment2, attachment3)
                if item is not None
            ],
        )
        if draft is None:
            await keyboards.respond(interaction, texts.SUBMIT_FAILED)
            return
        files = draft_preview_files(draft)
        await keyboards.respond(
            interaction,
            texts.draft_created(int(draft.id or 0)),
            view=self.build_draft_view(draft),
            embed=build_draft_embed(draft),
            files=files or None,
        )

    # --- shared --------------------------------------------------------------

    async def create_draft(
        self,
        *,
        user: discord.abc.User,
        guild_id: int | None,
        channel_id: int | None,
        message_id: int | None,
        text: str,
        media: list[MediaItem],
        blobs: dict[int, bytes] | None = None,
        attachments: list[discord.Attachment] | None = None,
    ) -> Submission | None:
        try:
            draft = await self.ctx.services.submissions.create_draft(
                source=Source.discord,
                author_platform_user_id=str(user.id),
                author_display_name=getattr(user, "display_name", None)
                or user.name,
                author_username=user.name,
                author_discord_profile_url=profile_url(user.id),
                text=text.strip() or None,
                media=media,
                guild_id=str(guild_id) if guild_id else None,
                source_chat_id=str(channel_id) if channel_id else None,
                source_message_id=str(message_id) if message_id else None,
            )
        except ValueError:
            logger.info("Черновик отклонён: текст длиннее лимита")
            return None
        if draft.id is None:
            return draft
        try:
            if blobs:
                draft = await self.ctx.services.submissions.cache_discord_blobs(
                    int(draft.id), blobs
                )
            elif attachments:
                read_blobs: dict[int, bytes] = {}
                media_with_url = [
                    item
                    for item in draft.media
                    if item.discord_attachment_url
                ]
                for item, attachment in zip(media_with_url, attachments):
                    data = await read_attachment_bytes(attachment)
                    if data:
                        read_blobs[item.order_index] = data
                if read_blobs:
                    draft = await self.ctx.services.submissions.cache_discord_blobs(
                        int(draft.id), read_blobs
                    )
            still_remote = any(
                item.ref_kind is RefKind.discord_url for item in draft.media
            )
            if still_remote:
                draft = await self.ctx.services.submissions.cache_discord_media(
                    int(draft.id)
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Кэш медиа заявки %s не удался — оставляем CDN URL", draft.id
            )
        return draft