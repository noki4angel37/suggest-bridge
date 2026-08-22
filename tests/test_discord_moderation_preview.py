"""Tests for Discord moderation card embed previews."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.adapters.discord.moderation import (
    build_card_embed,
    preview_filename_from_message,
)
from bot.core.models import (
    ContentType,
    MediaItem,
    Platform,
    Source,
    Submission,
    SubmissionStatus,
)


def _submission(**kwargs: object) -> Submission:
    base = dict(
        source=Source.telegram,
        author_platform_user_id="1",
        author_display_name="alice",
        text="привет",
        status=SubmissionStatus.pending,
        id=42,
        media=[],
    )
    base.update(kwargs)
    return Submission(**base)  # type: ignore[arg-type]


def test_build_card_embed_uses_attachment_protocol_for_tg_preview() -> None:
    embed = build_card_embed(
        _submission(
            media=[
                MediaItem(
                    content_type=ContentType.photo,
                    order_index=0,
                    file_id="AgAC",
                )
            ]
        ),
        attachment_image="preview_0.jpg",
    )
    assert embed.image is not None
    assert embed.image.url == "attachment://preview_0.jpg"


def test_build_card_embed_does_not_use_discord_cdn_url() -> None:
    """Discord will not render cdn.discordapp.com URLs inside embeds."""
    url = "https://cdn.discordapp.com/attachments/1/2/photo.png"
    embed = build_card_embed(
        _submission(
            source=Source.discord,
            media=[
                MediaItem(
                    content_type=ContentType.photo,
                    order_index=0,
                    discord_attachment_url=url,
                )
            ]
        )
    )
    assert embed.image.url is None


def test_preview_filename_from_message_prefers_image() -> None:
    message = SimpleNamespace(
        attachments=[
            SimpleNamespace(filename="preview_0.mp4"),
            SimpleNamespace(filename="preview_1.jpg"),
        ]
    )
    assert preview_filename_from_message(message) == "preview_1.jpg"


def test_build_card_embed_without_media_has_no_image() -> None:
    embed = build_card_embed(_submission())
    assert embed.image.url is None


def test_build_preview_files_copies_discord_cdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from bot.adapters.discord import moderation as mod

    async def fake_download(url: str, *, timeout_sec: float = 30.0) -> bytes | None:
        assert "cdn.discordapp.com" in url
        return b"\xff\xd8\xfffake-jpeg"

    monkeypatch.setattr(mod, "_download_url_bytes", fake_download)
    ctx = SimpleNamespace(telegram_bot=None)
    submission = _submission(
        source=Source.discord,
        media=[
            MediaItem(
                content_type=ContentType.photo,
                order_index=0,
                discord_attachment_url=(
                    "https://cdn.discordapp.com/attachments/1/2/a.png"
                ),
            )
        ],
    )
    files, embed_name = asyncio.run(mod._build_preview_files(ctx, submission))
    assert len(files) == 1
    assert files[0].filename == "preview_0.jpg"
    assert embed_name == "preview_0.jpg"


def test_build_preview_files_uses_local_path(tmp_path: Path) -> None:
    import asyncio

    from bot.adapters.discord import moderation as mod

    photo = tmp_path / "00_shot.png"
    photo.write_bytes(b"\xff\xd8\xfffake-jpeg")
    ctx = SimpleNamespace(telegram_bot=None)
    submission = _submission(
        source=Source.discord,
        media=[
            MediaItem(
                content_type=ContentType.photo,
                order_index=0,
                local_path=str(photo),
            )
        ],
    )
    files, embed_name = asyncio.run(mod._build_preview_files(ctx, submission))
    assert len(files) == 1
    assert files[0].filename == "preview_0.jpg"
    assert embed_name == "preview_0.jpg"
