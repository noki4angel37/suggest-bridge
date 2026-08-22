"""Pure domain rules: text limits, channel captions, status transitions."""

from __future__ import annotations

from bot.core.models import Source, Submission, SubmissionStatus
from bot.settings import anon_name as _anon_name
from bot.settings import suggest_hashtag as _suggest_hashtag

TEXT_LIMIT = 400


def __getattr__(name: str) -> object:
    if name == "HASHTAG":
        return _suggest_hashtag()
    if name == "ANON_NAME":
        return _anon_name()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Shown for submissions that came from Discord. Never use the English word "via".
SOURCE_LABELS: dict[Source, str | None] = {
    Source.telegram: None,
    Source.discord: "Discord",
}


def validate_text(text: str | None) -> str:
    """Normalize submission text and enforce the 400-character limit."""
    value = (text or "").strip()
    if len(value) > TEXT_LIMIT:
        raise ValueError(
            f"Текст длиннее {TEXT_LIMIT} символов: {len(value)}"
        )
    return value


def author_line(submission: Submission, *, with_author: bool) -> str:
    if with_author and submission.author_display_name.strip():
        return f"👤 {submission.author_display_name.strip()}"
    return f"👤 {_anon_name()}"


def source_line(submission: Submission) -> str | None:
    label = SOURCE_LABELS.get(submission.source)
    if not label:
        return None
    return f"🗨 {label}"


def build_channel_caption(
    submission: Submission,
    *,
    with_author: bool,
    author_line_override: str | None = None,
) -> str:
    """Build the channel post body.

    Subscriber posts end with the hashtag, admin posts never carry it.
    Discord-origin posts get a source line with the platform name.
    """
    parts: list[str] = []

    text = (submission.text or "").strip()
    if text:
        parts.append(text)

    meta: list[str] = [
        author_line_override.strip()
        if author_line_override and author_line_override.strip()
        else author_line(submission, with_author=with_author)
    ]
    mark = source_line(submission)
    if mark:
        meta.append(mark)
    parts.append("\n".join(meta))

    if not submission.is_admin_post:
        parts.append(_suggest_hashtag())

    return "\n\n".join(part for part in parts if part)


# --- status transitions ------------------------------------------------------

ALLOWED_TRANSITIONS: dict[SubmissionStatus, frozenset[SubmissionStatus]] = {
    SubmissionStatus.draft: frozenset(
        {
            SubmissionStatus.awaiting_privacy,
            SubmissionStatus.pending,
            SubmissionStatus.rejected,
        }
    ),
    SubmissionStatus.awaiting_privacy: frozenset(
        {
            SubmissionStatus.draft,
            SubmissionStatus.pending,
            SubmissionStatus.rejected,
        }
    ),
    SubmissionStatus.pending: frozenset(
        {
            SubmissionStatus.approved,
            SubmissionStatus.scheduled,
            SubmissionStatus.rejected,
        }
    ),
    SubmissionStatus.approved: frozenset(
        {
            SubmissionStatus.scheduled,
            SubmissionStatus.published,
            SubmissionStatus.rejected,
        }
    ),
    SubmissionStatus.scheduled: frozenset(
        {
            SubmissionStatus.approved,
            SubmissionStatus.published,
            SubmissionStatus.rejected,
        }
    ),
    SubmissionStatus.published: frozenset(),
    SubmissionStatus.rejected: frozenset(),
}

# Moderation decision already taken: repeated calls are no-ops, not errors.
HANDLED_STATUSES: frozenset[SubmissionStatus] = frozenset(
    {
        SubmissionStatus.approved,
        SubmissionStatus.scheduled,
        SubmissionStatus.published,
        SubmissionStatus.rejected,
    }
)

TERMINAL_STATUSES: frozenset[SubmissionStatus] = frozenset(
    {SubmissionStatus.published, SubmissionStatus.rejected}
)


def is_handled(status: SubmissionStatus) -> bool:
    return status in HANDLED_STATUSES


def is_terminal(status: SubmissionStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(
    current: SubmissionStatus, target: SubmissionStatus
) -> bool:
    if current is target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def ensure_transition(
    current: SubmissionStatus, target: SubmissionStatus
) -> None:
    if not can_transition(current, target):
        raise ValueError(
            f"Недопустимый переход статуса: {current.value} -> {target.value}"
        )
