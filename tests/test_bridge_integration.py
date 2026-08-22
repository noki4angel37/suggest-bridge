"""Integration tests of the composition root: one DB, one bus, both adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bot.adapters.discord.context import resolve_services
from bot.bridge import Bridge, build_bridge
from bot.config import BridgeConfig, RunMode
from bot.core import (
    ContentType,
    MediaItem,
    Platform,
    Source,
    Submission,
    SubmissionSubmitted,
    build_publish_plan,
    finalize_approval,
)

TEST_TOKEN = "123456:TEST-BRIDGE-TOKEN"
ADMIN_ID = 101
CHANNEL_ID = -1001234567890


@dataclass
class SentMessage:
    message_id: int


class FakeBot:
    """Records aiogram calls instead of performing them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        async def call(*args: Any, **kwargs: Any) -> SentMessage:
            self.calls.append((name, args, kwargs))
            return SentMessage(message_id=len(self.calls))

        return call


def make_bridge(tmp_path: Path, *, discord_token: str | None = None) -> Bridge:
    """Full composition root on a throwaway DB and a bot that never calls out."""
    mode = RunMode.both if discord_token else RunMode.telegram_only
    config = BridgeConfig(
        run_mode=mode,
        bot_token=TEST_TOKEN,
        admin_ids=frozenset({ADMIN_ID}),
        channel_id=CHANNEL_ID,
        bridge_db_path=str(tmp_path / "bridge.db"),
        discord_token=discord_token,
    )
    return build_bridge(config, bot=FakeBot())  # type: ignore[arg-type]


async def make_pending(bridge: Bridge, *, source: Source = Source.discord) -> Submission:
    draft = await bridge.services.submissions.create_draft(
        source=source,
        author_platform_user_id="555",
        author_display_name="Автор",
        text="Предложение из теста",
        guild_id="42" if source is Source.discord else None,
    )
    return await bridge.services.submissions.submit(int(draft.id))


# --- wiring ------------------------------------------------------------------


def test_bridge_wires_one_db_bus_and_scheduler(tmp_path: Path) -> None:
    bridge = make_bridge(tmp_path)

    assert bridge.services.bus is bridge.bus
    assert bridge.scheduler.db is bridge.db
    assert bridge.scheduler.bus is bridge.bus
    # Approve-now and scheduled posts go through PublishRouter (TG and/or DS).
    assert bridge.scheduler.publish_callback == bridge.publish_router.publish
    assert bridge.services.publish_router is bridge.publish_router
    assert bridge.services.admins.is_admin(Platform.telegram, str(ADMIN_ID))
    assert bridge.dp.sub_routers


def test_discord_adapter_accepts_the_shared_container(tmp_path: Path) -> None:
    bridge = make_bridge(tmp_path)

    resolved = resolve_services(bridge.services)

    assert resolved.bus is bridge.bus
    assert resolved.guilds is bridge.services.guilds


def test_missing_discord_token_does_not_break_the_run(tmp_path: Path) -> None:
    bridge = make_bridge(tmp_path)

    asyncio.run(bridge.run_discord())


# --- events ------------------------------------------------------------------


def test_submit_reaches_both_platform_listeners(tmp_path: Path) -> None:
    bridge = make_bridge(tmp_path)
    seen: dict[str, int] = {"telegram": 0, "discord": 0}

    async def telegram_listener(event: SubmissionSubmitted) -> None:
        seen["telegram"] += 1

    async def discord_listener(event: SubmissionSubmitted) -> None:
        seen["discord"] += 1

    bridge.bus.subscribe(SubmissionSubmitted, telegram_listener)
    bridge.bus.subscribe(SubmissionSubmitted, discord_listener)

    asyncio.run(make_pending(bridge))

    assert seen == {"telegram": 1, "discord": 1}
    # The adapter's own event sync is attached by the composition root, so the
    # moderation card was sent without anyone subscribing by hand.
    assert bridge.bot.calls


# --- approve ------------------------------------------------------------------


def test_double_approve_is_idempotent(tmp_path: Path) -> None:
    bridge = make_bridge(tmp_path)
    published: list[int] = []

    async def publish_cb(submission: Submission) -> tuple[str, str]:
        published.append(int(submission.id))
        return str(CHANNEL_ID), "77"

    async def scenario() -> tuple[Any, Any]:
        submission = await make_pending(bridge)
        first = await approve(bridge, int(submission.id), publish_cb)
        second = await approve(bridge, int(submission.id), publish_cb)
        return first, second

    first, second = asyncio.run(scenario())

    assert first.published and not first.already_handled
    assert second.already_handled
    assert published == [first.submission.id]


def test_concurrent_approve_publishes_once(tmp_path: Path) -> None:
    bridge = make_bridge(tmp_path)
    published: list[int] = []

    async def publish_cb(submission: Submission) -> tuple[str, str]:
        # Suspend inside the publish call: the competing coroutine gets its turn
        # exactly where a real network call would yield control.
        await asyncio.sleep(0.01)
        published.append(int(submission.id))
        return str(CHANNEL_ID), "77"

    async def scenario() -> list[Any]:
        submission = await make_pending(bridge)
        submission_id = int(submission.id)
        return list(
            await asyncio.gather(
                approve(bridge, submission_id, publish_cb),
                approve(bridge, submission_id, publish_cb),
            )
        )

    outcomes = asyncio.run(scenario())

    assert len(published) == 1
    assert sum(1 for outcome in outcomes if outcome.published) == 1
    assert sum(1 for outcome in outcomes if outcome.already_handled) == 1


async def approve(bridge: Bridge, submission_id: int, publish_cb: Any) -> Any:
    return await finalize_approval(
        bridge.services.moderation,
        submission_id=submission_id,
        with_author=True,
        publish_at=None,
        publish_now_cb=publish_cb,
        submissions=bridge.services.submissions,
        moderator_platform=Platform.discord,
        moderator_id="9001",
    )


# --- caption ------------------------------------------------------------------


@pytest.mark.parametrize("with_author", [True, False])
def test_caption_marks_discord_without_via(tmp_path: Path, with_author: bool) -> None:
    bridge = make_bridge(tmp_path)

    async def scenario() -> Submission:
        submission = await make_pending(bridge)
        return await bridge.services.submissions.attach_media(
            int(submission.id),
            [
                MediaItem(
                    content_type=ContentType.photo,
                    discord_attachment_url=(
                        "https://cdn.discordapp.com/attachments/1/2/pic.png"
                    ),
                )
            ],
        )

    submission = asyncio.run(scenario())
    caption = build_publish_plan(submission, with_author=with_author).caption

    assert "Discord" in caption
    assert "via" not in caption.lower()
    assert caption.endswith("#предложка")
