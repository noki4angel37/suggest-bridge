"""discord.ui views, buttons and modals with Russian labels.

Views know nothing about the domain: every button gets an injected coroutine,
so `suggest.py` and `moderation.py` own the logic.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord

from bot.adapters.discord import texts
from bot.core.rules import TEXT_LIMIT

logger = logging.getLogger(__name__)

DRAFT_TIMEOUT_SEC = 15 * 60
MODAL_TIMEOUT_SEC = 10 * 60
REJECT_REASON_LIMIT = 200
REPLY_TEXT_LIMIT = 1000

InteractionHandler = Callable[[discord.Interaction], Awaitable[None]]
PrivacyHandler = Callable[[discord.Interaction, bool], Awaitable[None]]
ValueHandler = Callable[[discord.Interaction, str], Awaitable[None]]


async def respond(
    interaction: discord.Interaction,
    text: str,
    *,
    ephemeral: bool = True,
    view: discord.ui.View | None = None,
    embed: discord.Embed | None = None,
    files: list[discord.File] | None = None,
) -> None:
    """Answer an interaction whether or not it was already deferred."""
    kwargs: dict[str, object] = {"ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view
    if embed is not None:
        kwargs["embed"] = embed
    if files:
        kwargs["files"] = files
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, **kwargs)  # type: ignore[arg-type]
        else:
            await interaction.response.send_message(text, **kwargs)  # type: ignore[arg-type]
    except discord.HTTPException:
        logger.warning("Не удалось ответить на взаимодействие")

class CallbackButton(discord.ui.Button["discord.ui.View"]):
    """Button that delegates to an injected coroutine."""

    def __init__(self, *, handler: InteractionHandler, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class PromptModal(discord.ui.Modal):
    """Single-field modal used for text, reject reason, reply and schedule."""

    def __init__(
        self,
        *,
        title: str,
        label: str,
        handler: ValueHandler,
        default: str | None = None,
        placeholder: str | None = None,
        max_length: int = TEXT_LIMIT,
        required: bool = True,
        style: discord.TextStyle = discord.TextStyle.paragraph,
    ) -> None:
        super().__init__(title=title[:45], timeout=MODAL_TIMEOUT_SEC)
        self._handler = handler
        self.field: discord.ui.TextInput[PromptModal] = discord.ui.TextInput(
            label=label[:45],
            default=default,
            placeholder=placeholder,
            max_length=max_length,
            required=required,
            style=style,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction, str(self.field.value or "").strip())


class DraftView(discord.ui.View):
    """Anonymity choice, submit and edit for a draft submission."""

    def __init__(
        self,
        submission_id: int,
        *,
        on_privacy: PrivacyHandler,
        on_submit: InteractionHandler,
        on_edit: InteractionHandler,
        on_cancel: InteractionHandler | None = None,
        want_anonymous: bool | None = None,
        author_id: int | None = None,
        timeout: float | None = DRAFT_TIMEOUT_SEC,
    ) -> None:
        super().__init__(timeout=timeout)
        self.submission_id = submission_id
        self.author_id = author_id
        self.message: discord.Message | None = None

        async def choose_anonymous(interaction: discord.Interaction) -> None:
            await on_privacy(interaction, True)

        async def choose_named(interaction: discord.Interaction) -> None:
            await on_privacy(interaction, False)

        self.add_item(
            CallbackButton(
                handler=choose_anonymous,
                label=texts.BTN_ANONYMOUS,
                style=(
                    discord.ButtonStyle.success
                    if want_anonymous is True
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"draft:anon:{submission_id}",
                row=0,
            )
        )
        self.add_item(
            CallbackButton(
                handler=choose_named,
                label=texts.BTN_NAMED,
                style=(
                    discord.ButtonStyle.success
                    if want_anonymous is False
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"draft:named:{submission_id}",
                row=0,
            )
        )
        self.add_item(
            CallbackButton(
                handler=on_submit,
                label=texts.BTN_SUBMIT,
                style=discord.ButtonStyle.primary,
                custom_id=f"draft:submit:{submission_id}",
                disabled=want_anonymous is None,
                row=1,
            )
        )
        self.add_item(
            CallbackButton(
                handler=on_edit,
                label=texts.BTN_EDIT,
                style=discord.ButtonStyle.secondary,
                custom_id=f"draft:edit:{submission_id}",
                row=1,
            )
        )
        if on_cancel is not None:
            self.add_item(
                CallbackButton(
                    handler=on_cancel,
                    label=texts.BTN_CANCEL_DRAFT,
                    style=discord.ButtonStyle.danger,
                    custom_id=f"draft:cancel:{submission_id}",
                    row=2,
                )
            )

    async def interaction_check(
        self, interaction: discord.Interaction
    ) -> bool:
        if self.author_id is None or interaction.user is None:
            return True
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            texts.DRAFT_NOT_YOURS, ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        disable_all(self)
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("Не удалось убрать кнопки черновика после таймаута")


class ModerationView(discord.ui.View):
    """Persistent moderation card controls (timeout=None + stable ids)."""

    def __init__(
        self,
        submission_id: int,
        *,
        on_approve: InteractionHandler,
        on_reject: InteractionHandler,
        on_reply: InteractionHandler,
        on_schedule: InteractionHandler,
        on_edit: InteractionHandler | None = None,
        on_target_tg: InteractionHandler | None = None,
        on_target_ds: InteractionHandler | None = None,
        on_target_both: InteractionHandler | None = None,
        can_decide: bool = True,
        can_schedule: bool = True,
        can_edit: bool = True,
        publish_target: str = "both",
    ) -> None:
        super().__init__(timeout=None)
        self.submission_id = submission_id
        self.add_item(
            CallbackButton(
                handler=on_approve,
                label=texts.BTN_APPROVE,
                style=discord.ButtonStyle.success,
                custom_id=f"mod:approve:{submission_id}",
                disabled=not can_decide,
                row=0,
            )
        )
        self.add_item(
            CallbackButton(
                handler=on_reject,
                label=texts.BTN_REJECT,
                style=discord.ButtonStyle.danger,
                custom_id=f"mod:reject:{submission_id}",
                disabled=not can_decide,
                row=0,
            )
        )
        self.add_item(
            CallbackButton(
                handler=on_schedule,
                label=texts.BTN_SCHEDULE,
                style=discord.ButtonStyle.primary,
                custom_id=f"mod:schedule:{submission_id}",
                disabled=not can_schedule,
                row=1,
            )
        )
        self.add_item(
            CallbackButton(
                handler=on_reply,
                label=texts.BTN_REPLY,
                style=discord.ButtonStyle.secondary,
                custom_id=f"mod:reply:{submission_id}",
                row=1,
            )
        )
        if on_edit is not None:
            self.add_item(
                CallbackButton(
                    handler=on_edit,
                    label=texts.BTN_EDIT_MOD,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"mod:edit:{submission_id}",
                    disabled=not can_edit,
                    row=1,
                )
            )
        if on_target_tg and on_target_ds and on_target_both:
            self.add_item(
                CallbackButton(
                    handler=on_target_tg,
                    label=texts.BTN_TARGET_TG,
                    style=(
                        discord.ButtonStyle.success
                        if publish_target == "telegram"
                        else discord.ButtonStyle.secondary
                    ),
                    custom_id=f"mod:tgt_tg:{submission_id}",
                    disabled=not can_decide,
                    row=2,
                )
            )
            self.add_item(
                CallbackButton(
                    handler=on_target_ds,
                    label=texts.BTN_TARGET_DS,
                    style=(
                        discord.ButtonStyle.success
                        if publish_target == "discord"
                        else discord.ButtonStyle.secondary
                    ),
                    custom_id=f"mod:tgt_ds:{submission_id}",
                    disabled=not can_decide,
                    row=2,
                )
            )
            self.add_item(
                CallbackButton(
                    handler=on_target_both,
                    label=texts.BTN_TARGET_BOTH,
                    style=(
                        discord.ButtonStyle.success
                        if publish_target == "both"
                        else discord.ButtonStyle.secondary
                    ),
                    custom_id=f"mod:tgt_both:{submission_id}",
                    disabled=not can_decide,
                    row=2,
                )
            )


class PassModerationView(discord.ui.View):
    """Persistent accept/reject controls for a temporary-role request."""

    def __init__(
        self,
        request_id: int,
        *,
        on_approve: InteractionHandler,
        on_reject: InteractionHandler,
        can_decide: bool = True,
    ) -> None:
        super().__init__(timeout=None)
        self.request_id = request_id
        self.add_item(
            CallbackButton(
                handler=on_approve,
                label="Принять",
                style=discord.ButtonStyle.success,
                custom_id=f"pass:approve:{request_id}",
                disabled=not can_decide,
                row=0,
            )
        )
        self.add_item(
            CallbackButton(
                handler=on_reject,
                label="Отклонить",
                style=discord.ButtonStyle.danger,
                custom_id=f"pass:reject:{request_id}",
                disabled=not can_decide,
                row=0,
            )
        )


def disable_all(view: discord.ui.View) -> discord.ui.View:
    for item in view.children:
        if isinstance(item, (discord.ui.Button, discord.ui.Select)):
            item.disabled = True
    return view


def edit_text_modal(
    submission_id: int, *, current: str | None, handler: ValueHandler
) -> PromptModal:
    return PromptModal(
        title=f"{texts.EDIT_MODAL_TITLE} №{submission_id}",
        label=texts.EDIT_MODAL_LABEL,
        handler=handler,
        default=current or None,
        max_length=TEXT_LIMIT,
        required=False,
    )


def reject_reason_modal(
    submission_id: int, *, handler: ValueHandler
) -> PromptModal:
    return PromptModal(
        title=f"{texts.REJECT_MODAL_TITLE} №{submission_id}",
        label=texts.REJECT_MODAL_LABEL,
        handler=handler,
        max_length=REJECT_REASON_LIMIT,
        required=False,
    )


def reply_modal(submission_id: int, *, handler: ValueHandler) -> PromptModal:
    return PromptModal(
        title=f"{texts.REPLY_MODAL_TITLE} №{submission_id}",
        label=texts.REPLY_MODAL_LABEL,
        handler=handler,
        max_length=REPLY_TEXT_LIMIT,
    )


def schedule_modal(
    submission_id: int, *, handler: ValueHandler
) -> PromptModal:
    return PromptModal(
        title=f"{texts.SCHEDULE_MODAL_TITLE} №{submission_id}",
        label=texts.SCHEDULE_MODAL_LABEL,
        handler=handler,
        placeholder="2026-08-12 19:30",
        max_length=32,
        style=discord.TextStyle.short,
    )
