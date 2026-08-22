from __future__ import annotations

from bot.core import rules
from bot.core.models import ContentType, MediaItem, RefKind, Source, Submission
from bot.core.publisher import (
    CAPTION_LIMIT,
    PublishMode,
    build_publish_plan,
    extract_publish_ref,
)


def make_submission(**kwargs: object) -> Submission:
    defaults: dict[str, object] = {
        "source": Source.telegram,
        "author_platform_user_id": "111",
        "author_display_name": "Пётр",
        "text": "Идея для канала",
        "want_anonymous": False,
    }
    defaults.update(kwargs)
    return Submission(**defaults)  # type: ignore[arg-type]


def photo(order_index: int = 0, file_id: str = "AgAC") -> MediaItem:
    return MediaItem(
        content_type=ContentType.photo,
        order_index=order_index,
        file_id=file_id,
    )


def test_subscriber_plan_has_hashtag() -> None:
    plan = build_publish_plan(make_submission())
    assert rules.HASHTAG in plan.caption
    assert "👤 Пётр" in plan.caption


def test_admin_plan_has_no_hashtag() -> None:
    plan = build_publish_plan(make_submission(is_admin_post=True))
    assert rules.HASHTAG not in plan.caption


def test_discord_plan_marks_source_without_via() -> None:
    plan = build_publish_plan(
        make_submission(source=Source.discord, author_display_name="Ivan")
    )
    assert "Discord" in plan.caption
    assert "via" not in plan.caption.lower()


def test_anonymous_submission_hides_author() -> None:
    plan = build_publish_plan(make_submission(want_anonymous=True))
    assert rules.ANON_NAME in plan.caption
    assert "Пётр" not in plan.caption
    assert plan.with_author is False


def test_explicit_with_author_overrides_stored_privacy() -> None:
    plan = build_publish_plan(make_submission(want_anonymous=True), with_author=True)
    assert "Пётр" in plan.caption
    assert plan.with_author is True


def test_unknown_privacy_defaults_to_anonymous() -> None:
    plan = build_publish_plan(make_submission(want_anonymous=None))
    assert plan.with_author is False
    assert rules.ANON_NAME in plan.caption


def test_bare_links_are_added_to_the_body() -> None:
    link = MediaItem(content_type=ContentType.link, caption="https://ya.ru")
    plan = build_publish_plan(make_submission(media=[link]))
    assert plan.mode is PublishMode.text
    assert plan.caption.startswith("https://ya.ru")
    assert plan.media == ()


def test_link_already_in_text_is_not_duplicated() -> None:
    link = MediaItem(content_type=ContentType.link, caption="https://ya.ru")
    plan = build_publish_plan(
        make_submission(text="Смотрите https://ya.ru", media=[link])
    )
    assert plan.caption.count("https://ya.ru") == 1


def test_discord_document_attachment_stays_media() -> None:
    document = MediaItem(
        content_type=ContentType.link,
        discord_attachment_url="https://cdn.discordapp.com/a/report.pdf",
    )
    plan = build_publish_plan(
        make_submission(source=Source.discord, media=[document])
    )
    assert plan.mode is PublishMode.single
    assert plan.media[0].needs_download is True


def test_text_only_submission_is_text_mode() -> None:
    plan = build_publish_plan(make_submission())
    assert plan.mode is PublishMode.text
    assert plan.media == ()
    assert plan.caption_as_separate_message is False


def test_single_photo_carries_the_caption() -> None:
    plan = build_publish_plan(make_submission(media=[photo()]))
    assert plan.mode is PublishMode.single
    assert plan.media[0].caption == plan.caption
    assert plan.caption_as_separate_message is False


def test_album_puts_caption_on_first_item_only() -> None:
    plan = build_publish_plan(
        make_submission(media=[photo(0, "one"), photo(1, "two")])
    )
    assert plan.mode is PublishMode.album
    assert plan.media[0].caption == plan.caption
    assert plan.media[1].caption is None
    assert len(plan.album_items) == 2
    assert plan.standalone_items == ()


def test_album_keeps_media_order() -> None:
    plan = build_publish_plan(
        make_submission(
            media=[photo(2, "third"), photo(0, "first"), photo(1, "second")]
        )
    )
    assert [item.file_ref for item in plan.media] == [
        "first",
        "second",
        "third",
    ]


def test_sticker_gets_caption_as_separate_message() -> None:
    sticker = MediaItem(content_type=ContentType.sticker, file_id="CAAC")
    plan = build_publish_plan(make_submission(media=[sticker]))
    assert plan.mode is PublishMode.single
    assert plan.caption_as_separate_message is True
    assert plan.media[0].caption is None


def test_album_with_sticker_splits_groupable_items() -> None:
    sticker = MediaItem(
        content_type=ContentType.sticker, order_index=1, file_id="CAAC"
    )
    plan = build_publish_plan(make_submission(media=[photo(0), sticker]))
    assert [item.content_type for item in plan.album_items] == [
        ContentType.photo
    ]
    assert [item.content_type for item in plan.standalone_items] == [
        ContentType.sticker
    ]
    assert plan.album_items[0].caption == plan.caption


def test_oversized_caption_goes_to_its_own_message() -> None:
    long_line = "a" * (CAPTION_LIMIT + 1)
    plan = build_publish_plan(
        make_submission(media=[photo()]), author_line_override=long_line
    )
    assert plan.caption_as_separate_message is True
    assert plan.media[0].caption is None


def test_discord_attachments_are_marked_for_download() -> None:
    attachment = MediaItem(
        content_type=ContentType.photo,
        discord_attachment_url="https://cdn.discordapp.com/a/b.png",
    )
    plan = build_publish_plan(
        make_submission(source=Source.discord, media=[attachment])
    )
    assert plan.needs_download is True
    assert plan.media[0].ref_kind is RefKind.discord_url


def test_telegram_file_ids_need_no_download() -> None:
    plan = build_publish_plan(make_submission(media=[photo()]))
    assert plan.needs_download is False


def test_media_without_ref_is_skipped() -> None:
    empty = MediaItem(content_type=ContentType.photo)
    plan = build_publish_plan(make_submission(media=[empty]))
    assert plan.mode is PublishMode.text
    assert plan.media == ()


def test_extract_publish_ref_reads_result_objects_and_tuples() -> None:
    class Result:
        target_id = -100
        message_id = 42

    assert extract_publish_ref(Result()) == ("-100", "42")
    assert extract_publish_ref(("-100", 42)) == ("-100", "42")
    assert extract_publish_ref(None) == (None, None)
