"""Russian inline keyboards for the Telegram adapter."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# --- callback data -----------------------------------------------------------

DRAFT_ANON = "anon"
DRAFT_NAMED = "named"
DRAFT_EDIT = "edit"
DRAFT_SUBMIT = "submit"
DRAFT_CANCEL = "cancel"

MOD_ACCEPT = "acc"
MOD_REJECT = "rej"
MOD_REPLY = "rep"
MOD_EDIT = "edit"
MOD_PUBLISH_ANON = "pub_a"
MOD_PUBLISH_NAMED = "pub_n"
MOD_TARGET_TG = "tgt_tg"
MOD_TARGET_DS = "tgt_ds"
MOD_TARGET_BOTH = "tgt_both"
MOD_SCHEDULE = "sched"
MOD_REJECT_SILENT = "rej_s"
MOD_REJECT_REASON = "rej_r"
MOD_BACK = "back"


class DraftCallback(CallbackData, prefix="sgd"):
    """Author-side buttons on a draft card."""

    action: str
    submission_id: int


class ModerationCallback(CallbackData, prefix="sgm"):
    """Admin-side buttons on a moderation card."""

    action: str
    submission_id: int


def _draft(action: str, submission_id: int) -> str:
    return DraftCallback(action=action, submission_id=submission_id).pack()


def _mod(action: str, submission_id: int) -> str:
    return ModerationCallback(action=action, submission_id=submission_id).pack()


# --- author keyboards --------------------------------------------------------


def _anonymity_row(
    submission_id: int, *, want_anonymous: bool | None
) -> list[InlineKeyboardButton]:
    anon_mark = "✅ " if want_anonymous is True else ""
    named_mark = "✅ " if want_anonymous is False else ""
    return [
        InlineKeyboardButton(
            text=f"{anon_mark}🕶 Анонимно",
            callback_data=_draft(DRAFT_ANON, submission_id),
        ),
        InlineKeyboardButton(
            text=f"{named_mark}👤 С моим именем",
            callback_data=_draft(DRAFT_NAMED, submission_id),
        ),
    ]


def anonymity_keyboard(
    submission_id: int, *, want_anonymous: bool | None = None
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_anonymity_row(submission_id, want_anonymous=want_anonymous)]
    )


def draft_keyboard(
    submission_id: int, *, want_anonymous: bool | None = None
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _anonymity_row(submission_id, want_anonymous=want_anonymous),
            [
                InlineKeyboardButton(
                    text="✏️ Изменить текст",
                    callback_data=_draft(DRAFT_EDIT, submission_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚀 Отправить на модерацию",
                    callback_data=_draft(DRAFT_SUBMIT, submission_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Отменить",
                    callback_data=_draft(DRAFT_CANCEL, submission_id),
                ),
            ],
        ]
    )


# --- moderation keyboards ----------------------------------------------------


def moderation_keyboard(submission_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=_mod(MOD_ACCEPT, submission_id),
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=_mod(MOD_REJECT, submission_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Править текст",
                    callback_data=_mod(MOD_EDIT, submission_id),
                ),
                InlineKeyboardButton(
                    text="💬 Ответить",
                    callback_data=_mod(MOD_REPLY, submission_id),
                ),
            ],
        ]
    )


def publish_keyboard(
    submission_id: int,
    *,
    allow_schedule: bool = True,
    publish_target: str = "both",
) -> InlineKeyboardMarkup:
    def mark(value: str, label: str) -> str:
        return f"✅ {label}" if publish_target == value else label

    rows = [
        [
            InlineKeyboardButton(
                text=mark("telegram", "📣 TG"),
                callback_data=_mod(MOD_TARGET_TG, submission_id),
            ),
            InlineKeyboardButton(
                text=mark("discord", "💬 DS"),
                callback_data=_mod(MOD_TARGET_DS, submission_id),
            ),
            InlineKeyboardButton(
                text=mark("both", "🔗 Оба"),
                callback_data=_mod(MOD_TARGET_BOTH, submission_id),
            ),
        ],
        [
            InlineKeyboardButton(
                text="🕶 Анонимно",
                callback_data=_mod(MOD_PUBLISH_ANON, submission_id),
            ),
            InlineKeyboardButton(
                text="👤 С именем",
                callback_data=_mod(MOD_PUBLISH_NAMED, submission_id),
            ),
        ],
    ]
    if allow_schedule:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🕓 Отложить публикацию",
                    callback_data=_mod(MOD_SCHEDULE, submission_id),
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="« Назад",
                callback_data=_mod(MOD_BACK, submission_id),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reject_keyboard(submission_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤐 Отклонить молча",
                    callback_data=_mod(MOD_REJECT_SILENT, submission_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Указать причину",
                    callback_data=_mod(MOD_REJECT_REASON, submission_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=_mod(MOD_BACK, submission_id),
                ),
            ],
        ]
    )


def schedule_prompt_keyboard(submission_id: int) -> InlineKeyboardMarkup:
    """Shown while the admin types a publish time — lets them step back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=_mod(MOD_BACK, submission_id),
                ),
            ],
        ]
    )
