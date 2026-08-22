from __future__ import annotations

from datetime import timezone

from bot.adapters.discord.content import (
    AttachmentInfo,
    build_media_items,
    classify_attachment,
    extract_links,
    has_content,
    parse_schedule_input,
)
from bot.core.models import ContentType


def test_classify_attachment_by_mime_and_suffix() -> None:
    assert classify_attachment("image/png", "a.png") is ContentType.photo
    assert classify_attachment("video/mp4", "a.mp4") is ContentType.video
    assert classify_attachment(None, "clip.MOV") is ContentType.video
    assert classify_attachment(None, "photo.JPEG") is ContentType.photo
    # Documents have no core counterpart and travel as an attachment link.
    assert classify_attachment("application/pdf", "doc.pdf") is ContentType.link
    assert classify_attachment(None, None) is ContentType.link


def test_build_media_items_keeps_order_and_urls() -> None:
    items = build_media_items(
        [
            AttachmentInfo("https://cdn/1.png", "image/png", "1.png"),
            AttachmentInfo("https://cdn/2.mp4", "video/mp4", "2.mp4"),
            AttachmentInfo("", "image/png", "skip.png"),
        ],
        ["https://cdn/sticker.png"],
    )
    assert [item.order_index for item in items] == [0, 1, 2]
    assert [item.content_type for item in items] == [
        ContentType.photo,
        ContentType.video,
        ContentType.sticker,
    ]
    assert items[0].discord_attachment_url == "https://cdn/1.png"
    assert items[0].file_id is None


def test_attachment_url_falls_back_to_proxy() -> None:
    from bot.adapters.discord.content import attachment_url

    assert attachment_url("https://cdn/a.png", "https://proxy/a.png") == (
        "https://cdn/a.png"
    )
    assert attachment_url("", "https://proxy/a.png") == "https://proxy/a.png"
    assert attachment_url(None, None) == ""


def test_extract_links_and_has_content() -> None:
    assert extract_links("смотри https://a.io и http://b.io/x") == [
        "https://a.io",
        "http://b.io/x",
    ]
    assert extract_links(None) == []
    assert has_content("  ", []) is False
    assert has_content("текст", []) is True


def test_parse_schedule_input_formats() -> None:
    parsed = parse_schedule_input("2026-08-12 19:30")
    assert parsed is not None
    assert parsed.tzinfo is timezone.utc
    assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 12)
    assert parse_schedule_input("12.08.2026 19:30") == parsed
    assert parse_schedule_input("2026-08-12T19:30") == parsed
    assert parse_schedule_input("завтра") is None
    assert parse_schedule_input("") is None
