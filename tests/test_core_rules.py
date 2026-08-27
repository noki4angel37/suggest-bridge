from __future__ import annotations

import pytest

from bot.core import rules
from bot.core.models import Source, Submission, SubmissionStatus


def make_submission(**kwargs: object) -> Submission:
    defaults: dict[str, object] = {
        "source": Source.telegram,
        "author_platform_user_id": "111",
        "author_display_name": "Пётр",
        "text": "Идея для канала",
    }
    defaults.update(kwargs)
    return Submission(**defaults)  # type: ignore[arg-type]


def test_display_sid() -> None:
    assert rules.display_sid(42) == "s42"
    assert rules.display_sid(None) == "s?"


def test_keyword_blocklist_match() -> None:
    words = rules.parse_keyword_blocklist("Спам, foo")
    assert rules.text_blocked_by_keywords("это Спам текст", words) == "спам"
    assert rules.text_blocked_by_keywords("чисто", words) is None
    assert rules.parse_keyword_blocklist("") == ()
    assert rules.parse_keyword_blocklist(None) == ()


def test_submission_filter_text_includes_captions() -> None:
    from bot.core.models import ContentType, MediaItem

    sub = make_submission(text="тело", media=[
        MediaItem(content_type=ContentType.photo, caption="реклама тут"),
    ])
    hay = rules.submission_filter_text(sub)
    assert "тело" in hay
    assert "реклама" in hay
    words = rules.parse_keyword_blocklist("реклама")
    assert rules.text_blocked_by_keywords(hay, words) == "реклама"


def test_validate_text_trims_and_allows_limit() -> None:
    assert rules.validate_text("  привет  ") == "привет"
    assert len(rules.validate_text("я" * rules.TEXT_LIMIT)) == rules.TEXT_LIMIT


def test_validate_text_rejects_over_limit() -> None:
    with pytest.raises(ValueError):
        rules.validate_text("я" * (rules.TEXT_LIMIT + 1))


def test_text_limit_is_400() -> None:
    assert rules.TEXT_LIMIT == 400


def test_subscriber_caption_has_hashtag() -> None:
    caption = rules.build_channel_caption(
        make_submission(), with_author=True
    )
    assert rules.HASHTAG in caption
    assert "👤 Пётр" in caption


def test_admin_post_caption_has_no_hashtag() -> None:
    caption = rules.build_channel_caption(
        make_submission(is_admin_post=True), with_author=False
    )
    assert rules.HASHTAG not in caption


def test_anonymous_caption_uses_anon_name() -> None:
    caption = rules.build_channel_caption(
        make_submission(), with_author=False
    )
    assert rules.ANON_NAME in caption
    assert "Пётр" not in caption


def test_discord_source_mark_without_via() -> None:
    caption = rules.build_channel_caption(
        make_submission(
            source=Source.discord,
            author_display_name="Ivan",
            text="Идея из Discord",
        ),
        with_author=True,
    )
    assert "Discord" in caption
    assert "via" not in caption.lower()


def test_telegram_source_has_no_platform_mark() -> None:
    caption = rules.build_channel_caption(
        make_submission(), with_author=True
    )
    assert "Discord" not in caption


def test_author_line_override_is_used() -> None:
    caption = rules.build_channel_caption(
        make_submission(),
        with_author=True,
        author_line_override="👤 Редакция",
    )
    assert "👤 Редакция" in caption
    assert "Пётр" not in caption


def test_caption_without_text_still_valid() -> None:
    caption = rules.build_channel_caption(
        make_submission(text=None), with_author=False
    )
    assert caption.startswith("👤")
    assert caption.endswith(rules.HASHTAG)


def test_transitions_allow_moderation_flow() -> None:
    assert rules.can_transition(
        SubmissionStatus.draft, SubmissionStatus.pending
    )
    assert rules.can_transition(
        SubmissionStatus.pending, SubmissionStatus.approved
    )
    assert rules.can_transition(
        SubmissionStatus.approved, SubmissionStatus.published
    )


def test_transitions_block_terminal_statuses() -> None:
    assert not rules.can_transition(
        SubmissionStatus.published, SubmissionStatus.pending
    )
    assert not rules.can_transition(
        SubmissionStatus.rejected, SubmissionStatus.approved
    )
    with pytest.raises(ValueError):
        rules.ensure_transition(
            SubmissionStatus.published, SubmissionStatus.approved
        )


def test_same_status_transition_is_idempotent() -> None:
    assert rules.can_transition(
        SubmissionStatus.published, SubmissionStatus.published
    )


def test_handled_and_terminal_helpers() -> None:
    assert rules.is_handled(SubmissionStatus.approved)
    assert not rules.is_handled(SubmissionStatus.pending)
    assert rules.is_terminal(SubmissionStatus.rejected)
    assert not rules.is_terminal(SubmissionStatus.approved)
