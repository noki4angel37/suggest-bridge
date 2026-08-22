from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

from bot.adapters.telegram import cards as cards_module
from bot.adapters.telegram import handlers_admin, keyboards, media
from bot.adapters.telegram.cards import TelegramCards, format_card
from bot.adapters.telegram.deps import TelegramServices
from bot.adapters.telegram.event_sync import TelegramEventSync
from bot.adapters.telegram.handlers_admin import parse_schedule_at
from bot.adapters.telegram.publisher import (
    TelegramPublisher,
    channel_caption,
    file_input,
)
from bot.adapters.telegram.router import build_telegram_router
from bot.core import (
    HASHTAG,
    AdminService,
    BridgeDatabase,
    ContentType,
    EventBus,
    MediaItem,
    ModerationService,
    Platform,
    RefKind,
    Source,
    Submission,
    SubmissionService,
    SubmissionStatus,
)

# --- fakes -------------------------------------------------------------------


@dataclass
class FakeFile:
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None


@dataclass
class FakeMessage:
    message_id: int
    photo: list[FakeFile] | None = None
    video: FakeFile | None = None
    animation: FakeFile | None = None
    sticker: FakeFile | None = None
    document: FakeFile | None = None
    caption: str | None = None
    text: str | None = None


@dataclass
class SentMessage:
    message_id: int


@dataclass
class FakeUser:
    id: int
    full_name: str = "Модератор"
    username: str | None = None


@dataclass
class FakeCallback:
    """Only the parts of CallbackQuery the moderation handlers touch."""

    from_user: FakeUser
    message: Any = None
    answers: list[str | None] = field(default_factory=list)

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append(text)


@dataclass
class FakeAdminMessage:
    """Minimal Message stand-in for admin text steps."""

    text: str
    from_user: FakeUser = field(default_factory=lambda: FakeUser(id=111))
    replies: list[str] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.replies.append(text)


class FakeState:
    """FSMContext stand-in."""

    def __init__(self, **data: Any) -> None:
        self.data = dict(data)
        self.state: Any = None

    async def get_state(self) -> Any:
        return self.state

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def update_data(self, **values: Any) -> None:
        self.data.update(values)


@dataclass
class FakeBot:
    """Records outgoing Telegram calls instead of performing them."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list
    )
    next_message_id: int = 100

    def _record(self, name: str, *args: Any, **kwargs: Any) -> SentMessage:
        self.calls.append((name, args, kwargs))
        self.next_message_id += 1
        return SentMessage(message_id=self.next_message_id)

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def only(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(args, kwargs) for call, args, kwargs in self.calls if call == name]

    async def send_message(self, *args: Any, **kwargs: Any) -> SentMessage:
        return self._record("send_message", *args, **kwargs)

    async def send_photo(self, *args: Any, **kwargs: Any) -> SentMessage:
        return self._record("send_photo", *args, **kwargs)

    async def send_video(self, *args: Any, **kwargs: Any) -> SentMessage:
        return self._record("send_video", *args, **kwargs)

    async def send_sticker(self, *args: Any, **kwargs: Any) -> SentMessage:
        return self._record("send_sticker", *args, **kwargs)

    async def send_media_group(self, *args: Any, **kwargs: Any) -> list[SentMessage]:
        self.calls.append(("send_media_group", args, kwargs))
        sent = []
        for _ in kwargs.get("media", []):
            self.next_message_id += 1
            sent.append(SentMessage(message_id=self.next_message_id))
        return sent

    async def edit_message_text(self, *args: Any, **kwargs: Any) -> SentMessage:
        return self._record("edit_message_text", *args, **kwargs)

    async def edit_message_caption(self, *args: Any, **kwargs: Any) -> SentMessage:
        return self._record("edit_message_caption", *args, **kwargs)


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> BridgeDatabase:
    return BridgeDatabase(str(tmp_path / "bridge.db"))


@pytest.fixture()
def bus() -> EventBus:
    return EventBus()


@pytest.fixture()
def submissions(db: BridgeDatabase, bus: EventBus) -> SubmissionService:
    return SubmissionService(db, bus)


@pytest.fixture()
def moderation(db: BridgeDatabase, bus: EventBus) -> ModerationService:
    return ModerationService(db, bus)


@pytest.fixture()
def admins(db: BridgeDatabase, bus: EventBus) -> AdminService:
    service = AdminService(db, bus)
    service.bootstrap_telegram_admins([111, 222])
    return service


def make_submission(
    service: SubmissionService,
    *,
    text: str | None = "Идея",
    media_items: list[MediaItem] | None = None,
    want_anonymous: bool | None = True,
    is_admin_post: bool = False,
    source: Source = Source.telegram,
    author_id: str = "777",
):
    draft = asyncio.run(
        service.create_draft(
            source=source,
            author_platform_user_id=author_id,
            author_display_name="Пётр",
            author_username="petya",
            text=text,
            media=media_items or [],
            is_admin_post=is_admin_post,
        )
    )
    assert draft.id is not None
    if want_anonymous is not None:
        asyncio.run(service.set_privacy(draft.id, want_anonymous=want_anonymous))
    return service.get(draft.id)


# --- media helpers -----------------------------------------------------------


def test_extract_urls_dedupes_and_trims() -> None:
    text = "Смотри https://example.com/a, и www.example.com/b. И снова https://example.com/a"
    assert media.extract_urls(text) == [
        "https://example.com/a",
        "www.example.com/b",
    ]


def test_extract_urls_ignores_plain_text() -> None:
    assert media.extract_urls("просто текст без ссылок") == []


def test_link_items_become_link_media() -> None:
    items = media.link_items("тут https://example.com", start_index=2)
    assert [item.content_type for item in items] == [ContentType.link]
    assert items[0].order_index == 2
    assert items[0].caption == "https://example.com"
    assert items[0].file_ref is None


def test_media_items_from_messages_keeps_album_order() -> None:
    batch = [
        FakeMessage(message_id=12, video=FakeFile("vid")),
        FakeMessage(message_id=10, photo=[FakeFile("small"), FakeFile("big")]),
        FakeMessage(message_id=11, photo=[FakeFile("second")], caption="Подпись"),
    ]
    items = media.media_items_from_messages(batch)
    assert [item.file_ref for item in items] == ["big", "second", "vid"]
    assert [item.order_index for item in items] == [0, 1, 2]
    assert [item.content_type for item in items] == [
        ContentType.photo,
        ContentType.photo,
        ContentType.video,
    ]
    assert media.album_caption(batch) == "Подпись"


def test_media_item_from_message_maps_types() -> None:
    assert (
        media.media_item_from_message(
            FakeMessage(message_id=1, sticker=FakeFile("stk"))
        ).content_type
        is ContentType.sticker
    )
    # GIFs arrive as animations and are published like videos.
    assert (
        media.media_item_from_message(
            FakeMessage(message_id=1, animation=FakeFile("gif"))
        ).content_type
        is ContentType.video
    )
    assert media.media_item_from_message(FakeMessage(message_id=1, text="hi")) is None
    image_doc = media.media_item_from_message(
        FakeMessage(
            message_id=1,
            document=FakeFile("docimg", mime_type="image/png", file_name="a.png"),
        )
    )
    assert image_doc is not None
    assert image_doc.content_type is ContentType.photo
    assert (
        media.media_item_from_message(
            FakeMessage(
                message_id=1,
                document=FakeFile("pdf", mime_type="application/pdf", file_name="a.pdf"),
            )
        )
        is None
    )


def test_album_buffer_flushes_batch_to_first_message() -> None:
    buffer = media.AlbumBuffer(delay=0)
    first = FakeMessage(message_id=2)
    second = FakeMessage(message_id=1)

    async def scenario() -> tuple[list[Any] | None, list[Any] | None]:
        leader = asyncio.create_task(buffer.collect("chat:group", first))
        await asyncio.sleep(0)
        follower = await buffer.collect("chat:group", second)
        return await leader, follower

    batch, follower = asyncio.run(scenario())
    assert follower is None
    assert [message.message_id for message in batch] == [1, 2]


def test_album_buffer_separates_groups() -> None:
    buffer = media.AlbumBuffer(delay=0)

    async def scenario() -> list[list[Any] | None]:
        return [
            await buffer.collect("chat:a", FakeMessage(message_id=1)),
            await buffer.collect("chat:b", FakeMessage(message_id=2)),
        ]

    first, second = asyncio.run(scenario())
    assert [m.message_id for m in first] == [1]
    assert [m.message_id for m in second] == [2]


def test_chunk_media_respects_telegram_limit() -> None:
    items = [
        MediaItem(content_type=ContentType.photo, order_index=i, file_id=f"f{i}")
        for i in range(12)
    ]
    chunks = media.chunk_media(items)
    assert [len(chunk) for chunk in chunks] == [10, 2]


def test_split_media_separates_kinds(submissions: SubmissionService) -> None:
    submission = make_submission(
        submissions,
        text="Смесь https://example.com",
        media_items=[
            MediaItem(content_type=ContentType.photo, order_index=0, file_id="p"),
            MediaItem(content_type=ContentType.sticker, order_index=1, file_id="s"),
        ],
    )
    split = media.split_media(submission)
    assert [item.file_ref for item in split.visual] == ["p"]
    assert [item.file_ref for item in split.stickers] == ["s"]
    assert split.is_single_visual is False


def test_split_media_includes_local_path_photo() -> None:
    submission = Submission(
        source=Source.discord,
        author_platform_user_id="1",
        author_display_name="ds",
        text=None,
        media=[
            MediaItem(
                content_type=ContentType.photo,
                order_index=0,
                local_path="/tmp/00_shot.jpg",
            )
        ],
    )
    split = media.split_media(submission)
    assert split.is_single_visual is True
    assert split.visual[0].local_path == "/tmp/00_shot.jpg"


# --- keyboards ---------------------------------------------------------------


def test_draft_keyboard_marks_current_privacy() -> None:
    markup = keyboards.draft_keyboard(7, want_anonymous=True)
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert "✅ 🕶 Анонимно" in labels
    assert "🚀 Отправить на модерацию" in labels

    packed = markup.inline_keyboard[0][0].callback_data
    parsed = keyboards.DraftCallback.unpack(packed)
    assert (parsed.action, parsed.submission_id) == (keyboards.DRAFT_ANON, 7)


def test_moderation_keyboards_callback_roundtrip() -> None:
    actions = []
    for markup in (
        keyboards.moderation_keyboard(5),
        keyboards.publish_keyboard(5),
        keyboards.reject_keyboard(5),
    ):
        for row in markup.inline_keyboard:
            for button in row:
                parsed = keyboards.ModerationCallback.unpack(button.callback_data)
                assert parsed.submission_id == 5
                actions.append(parsed.action)

    assert keyboards.MOD_SCHEDULE in actions
    assert keyboards.MOD_REJECT_SILENT in actions
    assert keyboards.MOD_REJECT_REASON in actions
    assert (
        keyboards.MOD_SCHEDULE
        not in [
            keyboards.ModerationCallback.unpack(button.callback_data).action
            for row in keyboards.publish_keyboard(5, allow_schedule=False).inline_keyboard
            for button in row
        ]
    )


# --- publisher ---------------------------------------------------------------


def test_channel_caption_hashtag_and_author(submissions: SubmissionService) -> None:
    anonymous = make_submission(submissions, want_anonymous=True)
    assert channel_caption(anonymous, with_author=False).endswith(HASHTAG)
    assert "Аноним" in channel_caption(anonymous, with_author=False)
    assert "Пётр" in channel_caption(anonymous, with_author=True)

    admin_post = make_submission(submissions, is_admin_post=True)
    assert HASHTAG not in channel_caption(admin_post, with_author=True)


def test_channel_caption_marks_discord_source(submissions: SubmissionService) -> None:
    submission = make_submission(submissions, source=Source.discord)
    caption = channel_caption(submission, with_author=True)
    assert "Discord" in caption
    assert "via" not in caption.lower()


def test_publish_text_only(submissions: SubmissionService) -> None:
    submission = make_submission(submissions, text="Просто текст")
    bot = FakeBot()
    publisher = TelegramPublisher(bot, -100500)

    result = asyncio.run(publisher.publish(submission))
    assert bot.names() == ["send_message"]
    args, kwargs = bot.only("send_message")[0]
    assert args[0] == -100500
    assert "Просто текст" in args[1]
    assert kwargs["parse_mode"] is None
    assert list(result.message_ids) == ["101"]
    assert result.chat_id == "-100500"


def test_publish_single_photo_uses_caption(submissions: SubmissionService) -> None:
    submission = make_submission(
        submissions,
        text="Подпись",
        media_items=[
            MediaItem(content_type=ContentType.photo, order_index=0, file_id="pic")
        ],
    )
    bot = FakeBot()
    asyncio.run(TelegramPublisher(bot, -1).publish(submission))

    assert bot.names() == ["send_photo"]
    args, kwargs = bot.only("send_photo")[0]
    assert args[1] == "pic"
    assert "Подпись" in kwargs["caption"]


def test_publish_album_captions_first_item_only(
    submissions: SubmissionService,
) -> None:
    submission = make_submission(
        submissions,
        text="Альбом",
        media_items=[
            MediaItem(content_type=ContentType.photo, order_index=0, file_id="a"),
            MediaItem(content_type=ContentType.video, order_index=1, file_id="b"),
        ],
    )
    bot = FakeBot()
    result = asyncio.run(TelegramPublisher(bot, -1).publish(submission))

    assert bot.names() == ["send_media_group"]
    _, kwargs = bot.only("send_media_group")[0]
    group = kwargs["media"]
    assert [item.type for item in group] == ["photo", "video"]
    assert "Альбом" in group[0].caption
    assert group[1].caption is None
    assert len(result.message_ids) == 2


def test_publish_sticker_sends_caption_separately(
    submissions: SubmissionService,
) -> None:
    submission = make_submission(
        submissions,
        text="К стикеру",
        media_items=[
            MediaItem(content_type=ContentType.sticker, order_index=0, file_id="stk")
        ],
    )
    bot = FakeBot()
    asyncio.run(TelegramPublisher(bot, -1).publish(submission))
    assert bot.names() == ["send_sticker", "send_message"]


def test_publish_appends_links_missing_from_text(
    submissions: SubmissionService,
) -> None:
    submission = make_submission(
        submissions,
        text="Без ссылки в тексте",
        media_items=[
            MediaItem(
                content_type=ContentType.link,
                order_index=0,
                caption="https://example.com/x",
            )
        ],
    )
    bot = FakeBot()
    asyncio.run(TelegramPublisher(bot, -1).publish(submission))
    args, _ = bot.only("send_message")[0]
    assert "https://example.com/x" in args[1]


def test_publish_keeps_link_from_text_once(submissions: SubmissionService) -> None:
    submission = make_submission(
        submissions,
        text="Ссылка https://example.com/x внутри",
        media_items=[
            MediaItem(
                content_type=ContentType.link,
                order_index=0,
                caption="https://example.com/x",
            )
        ],
    )
    bot = FakeBot()
    asyncio.run(TelegramPublisher(bot, -1).publish(submission))
    args, _ = bot.only("send_message")[0]
    assert args[1].count("https://example.com/x") == 1


def test_publish_and_mark_sets_published(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    submission = make_submission(submissions)
    asyncio.run(submissions.submit(submission.id))
    asyncio.run(moderation.approve(submission.id))

    bot = FakeBot()
    publisher = TelegramPublisher(bot, -100777, moderation=moderation)
    result = asyncio.run(publisher.publish_and_mark(submissions.get(submission.id)))

    stored = submissions.get(submission.id)
    assert stored.status is SubmissionStatus.published
    assert result.chat_id == "-100777"
    refs = moderation.get_moderation_refs(submission.id, platform=Platform.telegram)
    assert refs == []


# --- cards -------------------------------------------------------------------


def test_format_card_escapes_and_shows_wish(submissions: SubmissionService) -> None:
    submission = make_submission(submissions, text="<b>хак</b>", want_anonymous=True)
    body = format_card(submission)
    assert "&lt;b&gt;хак&lt;/b&gt;" in body
    assert "Автор просит анонимность" in body
    assert "На модерации" in body or "Черновик" in body


def test_format_card_marks_admin_post(submissions: SubmissionService) -> None:
    submission = make_submission(submissions, is_admin_post=True)
    assert "Пост администратора" in format_card(submission)


def test_send_cards_reaches_every_admin(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
) -> None:
    submission = make_submission(submissions)
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)

    refs = asyncio.run(cards.send_cards(submission))
    assert sorted(ref.target_id for ref in refs) == ["111", "222"]
    assert bot.names() == ["send_message", "send_message"]
    _, kwargs = bot.only("send_message")[0]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"] is not None

    stored = moderation.get_moderation_refs(submission.id, platform=Platform.telegram)
    assert len(stored) == 2


def test_album_card_sends_preview_then_text(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
) -> None:
    submission = make_submission(
        submissions,
        media_items=[
            MediaItem(content_type=ContentType.photo, order_index=0, file_id="a"),
            MediaItem(content_type=ContentType.photo, order_index=1, file_id="b"),
        ],
    )
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    asyncio.run(cards.send_cards(submission))

    assert bot.names() == [
        "send_media_group",
        "send_message",
        "send_media_group",
        "send_message",
    ]
    assert cards_module.card_carries_media(submission) is False


def test_update_cards_edits_text_and_drops_keyboard(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
) -> None:
    submission = make_submission(submissions)
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    asyncio.run(cards.send_cards(submission))
    bot.calls.clear()

    asyncio.run(moderation.reject(submission.id, reason="Не по теме"))
    asyncio.run(cards.update_cards(submissions.get(submission.id)))

    edits = bot.only("edit_message_text")
    assert len(edits) == 2
    _, kwargs = edits[0]
    assert kwargs["reply_markup"] is None
    assert "Отклонено" in kwargs["text"] if "text" in kwargs else True
    assert set(kwargs) >= {"chat_id", "message_id", "parse_mode"}


def test_photo_card_is_edited_by_caption(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
) -> None:
    submission = make_submission(
        submissions,
        media_items=[
            MediaItem(content_type=ContentType.photo, order_index=0, file_id="pic")
        ],
    )
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    asyncio.run(cards.send_cards(submission))
    assert bot.names() == ["send_photo", "send_photo"]

    bot.calls.clear()
    asyncio.run(cards.update_cards(submission, status_line="✅ Опубликовано"))
    assert bot.names() == ["edit_message_caption", "edit_message_caption"]
    _, kwargs = bot.only("edit_message_caption")[0]
    assert "✅ Опубликовано" in kwargs["caption"]


def test_file_input_rejects_discord_cdn_url() -> None:
    item = MediaItem(
        content_type=ContentType.photo,
        order_index=0,
        discord_attachment_url="https://cdn.discordapp.com/a.jpg",
    )
    assert item.ref_kind is RefKind.discord_url
    with pytest.raises(ValueError, match="без файла"):
        file_input(item)


def test_discord_photo_card_without_cache_is_text(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
) -> None:
    submission = make_submission(
        submissions,
        source=Source.discord,
        media_items=[
            MediaItem(
                content_type=ContentType.photo,
                order_index=0,
                discord_attachment_url="https://cdn.discordapp.com/a.jpg",
            )
        ],
    )
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    asyncio.run(cards.send_cards(submission))
    assert "send_photo" not in bot.names()
    assert bot.names() == ["send_message", "send_message"]


def test_status_line_shows_publish_mode(submissions: SubmissionService) -> None:
    submission = make_submission(submissions, want_anonymous=False)
    submission.status = SubmissionStatus.published
    assert cards_module.status_line_for(submission) == "✅ Опубликовано (с именем)"


# --- event sync --------------------------------------------------------------


def test_submitted_event_sends_cards(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
    bus: EventBus,
) -> None:
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    TelegramEventSync(bot, cards=cards).attach(bus)

    submission = make_submission(submissions)
    bot.calls.clear()
    asyncio.run(submissions.submit(submission.id))

    assert bot.names() == ["send_message", "send_message"]
    assert len(moderation.get_moderation_refs(submission.id)) == 2


def test_reject_with_reason_notifies_author(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
    bus: EventBus,
) -> None:
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    TelegramEventSync(bot, cards=cards).attach(bus)

    submission = make_submission(submissions, author_id="777")
    asyncio.run(submissions.submit(submission.id))
    bot.calls.clear()
    asyncio.run(moderation.reject(submission.id, reason="Не по теме"))

    author_messages = [
        args for args, _ in bot.only("send_message") if args and args[0] == 777
    ]
    assert len(author_messages) == 1
    assert "Не по теме" in author_messages[0][1]


def test_silent_reject_keeps_author_uninformed(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
    bus: EventBus,
) -> None:
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    TelegramEventSync(bot, cards=cards).attach(bus)

    submission = make_submission(submissions)
    asyncio.run(submissions.submit(submission.id))
    bot.calls.clear()
    asyncio.run(moderation.reject(submission.id))

    assert bot.only("send_message") == []
    assert len(bot.only("edit_message_text")) == 2


def test_discord_author_gets_no_telegram_dm(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
    bus: EventBus,
) -> None:
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    TelegramEventSync(bot, cards=cards).attach(bus)

    submission = make_submission(submissions, source=Source.discord, author_id="42")
    asyncio.run(submissions.submit(submission.id))
    bot.calls.clear()
    asyncio.run(moderation.reject(submission.id, reason="Оффтоп"))

    assert bot.only("send_message") == []


def test_published_event_notifies_telegram_author(
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
    bus: EventBus,
) -> None:
    bot = FakeBot()
    cards = TelegramCards(bot, moderation=moderation, admins=admins)
    TelegramEventSync(bot, cards=cards).attach(bus)

    submission = make_submission(submissions)
    asyncio.run(submissions.submit(submission.id))
    asyncio.run(moderation.approve(submission.id))
    bot.calls.clear()

    publisher = TelegramPublisher(bot, -100500, moderation=moderation)
    asyncio.run(publisher.publish_and_mark(submissions.get(submission.id)))

    channel_posts = [
        args for args, _ in bot.only("send_message") if args and args[0] == -100500
    ]
    author_posts = [
        args for args, _ in bot.only("send_message") if args and args[0] == 777
    ]
    assert len(channel_posts) == 1
    assert len(author_posts) == 1
    assert "опубликована" in author_posts[0][1]


# --- schedule parsing --------------------------------------------------------


def test_parse_schedule_relative_minutes_and_hours() -> None:
    now = datetime(2026, 8, 12, 10, 0).astimezone()
    assert parse_schedule_at("+30м", now=now) == (now + timedelta(minutes=30)).astimezone(
        parse_schedule_at("+30м", now=now).tzinfo
    )
    assert parse_schedule_at("+2ч", now=now) - now == timedelta(hours=2)
    assert parse_schedule_at("+45", now=now) - now == timedelta(minutes=45)
    assert parse_schedule_at("+0", now=now) is None


def test_parse_schedule_time_rolls_to_tomorrow() -> None:
    now = datetime(2026, 8, 12, 20, 0).astimezone()
    tomorrow = parse_schedule_at("09:15", now=now)
    assert tomorrow is not None
    assert tomorrow.astimezone().strftime("%d.%m %H:%M") == "13.08 09:15"

    later_today = parse_schedule_at("23:45", now=now)
    assert later_today.astimezone().strftime("%d.%m %H:%M") == "12.08 23:45"


def test_parse_schedule_explicit_date() -> None:
    now = datetime(2026, 8, 12, 20, 0).astimezone()
    moment = parse_schedule_at("31.12.2026 18:00", now=now)
    assert moment is not None
    assert moment.astimezone().strftime("%d.%m.%Y %H:%M") == "31.12.2026 18:00"
    # A bare date already gone this year means next year.
    assert parse_schedule_at("01.01 10:00", now=now).astimezone().year == 2027


def test_parse_schedule_rejects_garbage() -> None:
    now = datetime(2026, 8, 12, 20, 0).astimezone()
    for raw in ("", "потом", "99:99", "31.02 10:00", "10:00 завтра", None):
        assert parse_schedule_at(raw, now=now) is None


# --- moderation handlers and wiring ------------------------------------------


ADMIN_ID = 111


@pytest.fixture()
def services(db: BridgeDatabase, bus: EventBus) -> TelegramServices:
    built = TelegramServices.from_database(
        db, bot=FakeBot(), channel_id=-100500, bus=bus
    )
    built.admins.bootstrap_telegram_admins([ADMIN_ID])
    return built


class RecordingSession:
    """aiogram session stub: records outgoing methods, never touches network."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def __call__(self, bot: Any, method: Any, timeout: Any = None) -> Message:
        self.requests.append(method)
        return Message(
            message_id=999,
            date=datetime.now(timezone.utc),
            chat=Chat(id=1, type="private"),
        )

    async def close(self) -> None:
        return None


@dataclass
class DispatcherHarness:
    """Real Dispatcher + real adapter router over a stubbed transport."""

    dispatcher: Dispatcher
    bot: Bot
    services: TelegramServices
    session: RecordingSession
    update_id: int = 0

    async def feed(self, *, user_id: int, text: str) -> None:
        self.update_id += 1
        update = Update(
            update_id=self.update_id,
            message=Message(
                message_id=self.update_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="Пётр"),
                text=text,
            ),
        )
        await self.dispatcher.feed_update(self.bot, update)


@pytest.fixture()
def dispatcher(db: BridgeDatabase, bus: EventBus) -> DispatcherHarness:
    bot = Bot(token="123456789:AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPPQQR")
    session = RecordingSession()
    bot.session = session  # type: ignore[assignment]
    services = TelegramServices.from_database(
        db, bot=bot, channel_id=-100500, bus=bus
    )
    services.admins.bootstrap_telegram_admins([ADMIN_ID])
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_telegram_router(services))
    return DispatcherHarness(
        dispatcher=dp, bot=bot, services=services, session=session
    )


def pending_submission(services: TelegramServices) -> int:
    submission = make_submission(services.submissions)
    asyncio.run(services.submissions.submit(submission.id))
    return submission.id


def publish_named(services: TelegramServices, submission_id: int) -> FakeCallback:
    callback = FakeCallback(from_user=FakeUser(id=111))
    asyncio.run(
        handlers_admin.publish_with_name(
            callback,
            keyboards.ModerationCallback(
                action=keyboards.MOD_PUBLISH_NAMED, submission_id=submission_id
            ),
            services,
        )
    )
    return callback


def channel_posts(bot: FakeBot) -> list[tuple[Any, ...]]:
    return [args for args, _ in bot.only("send_message") if args and args[0] == -100500]


def author_posts(bot: FakeBot) -> list[tuple[Any, ...]]:
    return [args for args, _ in bot.only("send_message") if args and args[0] == 777]


def test_build_telegram_router_orders_flows_and_attaches_events(
    services: TelegramServices,
) -> None:
    router = build_telegram_router(services)
    assert [child.name for child in router.sub_routers] == [
        "tg-admin",
        "tg-reply",
        "admin_settings",
        "admin_host",
        "tg-user",
    ]
    assert len(router.message.outer_middleware) == 1
    assert len(router.callback_query.outer_middleware) == 1
    assert services.guilds is not None

    # Events are live: submitting now fans a card out to the seeded admin.
    pending_submission(services)
    assert services.bot.names() == ["send_message"]


def test_publish_named_marks_published_and_notifies_author(
    services: TelegramServices,
) -> None:
    services.events.attach(services.bus)
    submission_id = pending_submission(services)
    services.bot.calls.clear()

    publish_named(services, submission_id)

    stored = services.submissions.get(submission_id)
    assert stored.status is SubmissionStatus.published
    assert stored.want_anonymous is False
    assert len(channel_posts(services.bot)) == 1
    assert "Пётр" in channel_posts(services.bot)[0][1]
    assert len(author_posts(services.bot)) == 1
    assert len(services.bot.only("edit_message_text")) >= 1


def test_second_publish_click_is_ignored(services: TelegramServices) -> None:
    services.events.attach(services.bus)
    submission_id = pending_submission(services)
    publish_named(services, submission_id)
    services.bot.calls.clear()

    callback = publish_named(services, submission_id)
    assert "Заявка уже обработана." in callback.answers
    assert channel_posts(services.bot) == []


def test_publish_retry_after_channel_error(db: BridgeDatabase, bus: EventBus) -> None:
    class FlakyBot(FakeBot):
        failures: int = 1

        async def send_message(self, *args: Any, **kwargs: Any) -> SentMessage:
            if args and args[0] == -100500 and self.failures > 0:
                self.failures -= 1
                raise TelegramAPIError(method=None, message="канал недоступен")
            return await super().send_message(*args, **kwargs)

    services = TelegramServices.from_database(
        db, bot=FlakyBot(), channel_id=-100500, bus=bus
    )
    services.admins.bootstrap_telegram_admins([111])
    services.events.attach(services.bus)
    submission_id = pending_submission(services)

    failed = publish_named(services, submission_id)
    assert any("Ошибка публикации" in (text or "") for text in failed.answers)
    assert services.submissions.get(submission_id).status is SubmissionStatus.approved

    publish_named(services, submission_id)
    assert services.submissions.get(submission_id).status is SubmissionStatus.published
    assert len(channel_posts(services.bot)) == 1


def test_silent_reject_handler_keeps_author_quiet(
    services: TelegramServices,
) -> None:
    services.events.attach(services.bus)
    submission_id = pending_submission(services)
    services.bot.calls.clear()

    callback = FakeCallback(from_user=FakeUser(id=111))
    asyncio.run(
        handlers_admin.reject_silent(
            callback,
            keyboards.ModerationCallback(
                action=keyboards.MOD_REJECT_SILENT, submission_id=submission_id
            ),
            services,
        )
    )

    stored = services.submissions.get(submission_id)
    assert stored.status is SubmissionStatus.rejected
    assert stored.reject_reason is None
    assert author_posts(services.bot) == []


def test_schedule_apply_sets_scheduled_status(services: TelegramServices) -> None:
    services.events.attach(services.bus)
    submission_id = pending_submission(services)
    state = FakeState(submission_id=submission_id)
    message = FakeAdminMessage(text="+2ч")

    asyncio.run(handlers_admin.schedule_apply(message, services, state))

    stored = services.submissions.get(submission_id)
    assert stored.status is SubmissionStatus.scheduled
    assert stored.scheduled_at is not None
    assert stored.want_anonymous is True
    assert state.state is None
    assert "отложена до" in message.replies[0]
    # Nothing was published: the core scheduler owns that moment.
    assert channel_posts(services.bot) == []


def test_dispatcher_routes_private_text_into_draft_flow(
    dispatcher: DispatcherHarness,
) -> None:
    asyncio.run(dispatcher.feed(user_id=555, text="Идея для канала"))

    drafts = dispatcher.services.submissions.list_by_status(SubmissionStatus.draft)
    assert [draft.text for draft in drafts] == ["Идея для канала"]
    reply = dispatcher.session.requests[-1]
    assert "Черновик" in reply.text
    assert len(reply.reply_markup.inline_keyboard) == 4


def test_dispatcher_prefers_admin_commands_over_draft_catch_all(
    dispatcher: DispatcherHarness,
) -> None:
    asyncio.run(dispatcher.feed(user_id=ADMIN_ID, text="/admins"))
    assert "Админы" in dispatcher.session.requests[-1].text

    # A command from a subscriber must never turn into a submission.
    asyncio.run(dispatcher.feed(user_id=555, text="/admins"))
    assert "Неизвестная команда" in dispatcher.session.requests[-1].text
    assert dispatcher.services.submissions.list_by_status(SubmissionStatus.draft) == []


def test_schedule_apply_rejects_bad_time(services: TelegramServices) -> None:
    submission_id = pending_submission(services)
    state = FakeState(submission_id=submission_id)
    state.state = "AdminSchedule:waiting_datetime"
    message = FakeAdminMessage(text="когда-нибудь")

    asyncio.run(handlers_admin.schedule_apply(message, services, state))

    assert services.submissions.get(submission_id).status is SubmissionStatus.pending
    assert "Не понял время" in message.replies[0]
    # The admin stays in the prompt and can try again.
    assert state.state == "AdminSchedule:waiting_datetime"
