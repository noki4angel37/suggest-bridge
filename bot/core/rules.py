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


def display_sid(submission_id: int | None) -> str:
    """Stable short public id for cards and logs (derived from DB id)."""
    if submission_id is None:
        return "s?"
    return f"s{int(submission_id)}"


def parse_keyword_blocklist(raw: str | None) -> tuple[str, ...]:
    """Comma-separated keywords; empty tokens dropped; case-folded."""
    if not raw:
        return ()
    return tuple(
        part.strip().casefold()
        for part in raw.split(",")
        if part.strip()
    )


def text_blocked_by_keywords(text: str | None, keywords: tuple[str, ...]) -> str | None:
    """Return the first matching keyword (case-insensitive) or None."""
    if not keywords:
        return None
    hay = (text or "").casefold()
    if not hay:
        return None
    for word in keywords:
        if word and word in hay:
            return word
    return None


def submission_filter_text(submission: Submission) -> str:
    """Text + media captions for keyword filter."""
    parts: list[str] = []
    if submission.text:
        parts.append(submission.text)
    for item in submission.media:
        cap = (item.caption or "").strip()
        if cap:
            parts.append(cap)
    return "\n".join(parts)


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


def needs_publish_retry(submission: Submission) -> bool:
    """Approved but never marked published (publish callback failed)."""
    return (
        submission.status is SubmissionStatus.approved
        and submission.published_at is None
    )


def can_moderate_decide(submission: Submission) -> bool:
    if needs_publish_retry(submission):
        return True
    return not is_handled(submission.status)


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
