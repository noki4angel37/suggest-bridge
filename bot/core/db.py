"""SQLite storage for the suggest bridge (separate DB from submissions.db)."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from bot.core.models import (
    Admin,
    BlacklistEntry,
    ContentType,
    GuildConfig,
    MediaItem,
    MirrorKind,
    MirrorLink,
    ModerationRef,
    PassRequest,
    PassRequestStatus,
    Platform,
    PublishTarget,
    RefKind,
    Source,
    Submission,
    SubmissionStatus,
    utcnow,
)

DEFAULT_BRIDGE_DB_PATH = "./data/bridge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    author_platform_user_id TEXT NOT NULL,
    author_display_name TEXT NOT NULL,
    author_username TEXT,
    author_discord_profile_url TEXT,
    want_anonymous INTEGER,
    text TEXT,
    is_admin_post INTEGER NOT NULL DEFAULT 0,
    guild_id TEXT,
    source_chat_id TEXT,
    source_message_id TEXT,
    scheduled_at TEXT,
    published_at TEXT,
    reject_reason TEXT,
    publish_target TEXT NOT NULL DEFAULT 'both',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_author
    ON submissions(source, author_platform_user_id);

CREATE TABLE IF NOT EXISTS submission_media (
    submission_id INTEGER NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL,
    file_ref TEXT,
    ref_kind TEXT,
    caption TEXT,
    PRIMARY KEY (submission_id, order_index),
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS moderation_refs (
    submission_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    target_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (submission_id, platform, target_id),
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS admins (
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    added_by TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS guild_configs (
    guild_id TEXT PRIMARY KEY,
    suggest_channel_id TEXT,
    mod_channel_id TEXT,
    publish_channel_id TEXT,
    propose_role_ids TEXT NOT NULL DEFAULT '[]',
    mod_role_ids TEXT NOT NULL DEFAULT '[]',
    rate_limit_enabled INTEGER NOT NULL DEFAULT 0,
    rate_limit_count INTEGER,
    rate_limit_window_sec INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mirror_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    kind TEXT NOT NULL,
    tg_chat_id TEXT,
    tg_message_id TEXT,
    ds_guild_id TEXT,
    ds_channel_id TEXT,
    ds_message_id TEXT,
    submission_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mirror_tg
    ON mirror_links(tg_chat_id, tg_message_id);
CREATE INDEX IF NOT EXISTS idx_mirror_ds
    ON mirror_links(ds_channel_id, ds_message_id);

CREATE TABLE IF NOT EXISTS blacklist (
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS antiflood_hits (
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    window_start REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS pass_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    username TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    expires_at TEXT,
    cooldown_until TEXT,
    mod_channel_id TEXT,
    mod_message_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_pass_requests_user_status
    ON pass_requests(guild_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_pass_requests_status
    ON pass_requests(status);

CREATE TABLE IF NOT EXISTS pass_antiflood (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    window_start REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    last_hit REAL NOT NULL DEFAULT 0,
    strike_until REAL,
    PRIMARY KEY (guild_id, user_id)
);
"""

_SUBMISSION_UPDATABLE = (
    "status",
    "author_display_name",
    "author_username",
    "author_discord_profile_url",
    "want_anonymous",
    "text",
    "is_admin_post",
    "guild_id",
    "source_chat_id",
    "source_message_id",
    "scheduled_at",
    "published_at",
    "reject_reason",
    "publish_target",
)


def resolve_bridge_db_path() -> str:
    """Bridge DB location: BRIDGE_DB_PATH, then BRIDGE_DB fallback, then default.

    The legacy DB_PATH (submissions.db) is intentionally not reused: the bridge
    owns a separate schema and file.
    """
    for key in ("BRIDGE_DB_PATH", "BRIDGE_DB"):
        value = os.environ.get(key, "").strip()
        if value and value != "REPLACE_ME":
            return value
    return DEFAULT_BRIDGE_DB_PATH


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


class BridgeDatabase:
    """Schema owner and thin CRUD layer over the bridge SQLite file."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = Path(db_path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._tx() as conn:
            conn.executescript(SCHEMA)
            # Forward-compatible columns for older bridge DBs.
            for table, column, decl in (
                ("submissions", "guild_id", "TEXT"),
                ("submissions", "published_at", "TEXT"),
                ("submissions", "reject_reason", "TEXT"),
                ("submissions", "publish_target", "TEXT NOT NULL DEFAULT 'both'"),
                ("submission_media", "ref_kind", "TEXT"),
                ("guild_configs", "rate_limit_window_sec", "INTEGER"),
                ("guild_configs", "publish_channel_id", "TEXT"),
            ):
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {decl}"
                    )
                except sqlite3.OperationalError:
                    pass

    # --- submissions ---------------------------------------------------------

    def _row_to_submission(self, row: sqlite3.Row) -> Submission:
        keys = row.keys()
        raw_target = row["publish_target"] if "publish_target" in keys else "both"
        try:
            publish_target = PublishTarget(raw_target or "both")
        except ValueError:
            publish_target = PublishTarget.both
        return Submission(
            id=row["id"],
            source=Source(row["source"]),
            status=SubmissionStatus(row["status"]),
            author_platform_user_id=row["author_platform_user_id"],
            author_display_name=row["author_display_name"],
            author_username=row["author_username"],
            author_discord_profile_url=row["author_discord_profile_url"],
            want_anonymous=_to_bool(row["want_anonymous"]),
            text=row["text"],
            is_admin_post=bool(row["is_admin_post"]),
            guild_id=row["guild_id"],
            source_chat_id=row["source_chat_id"],
            source_message_id=row["source_message_id"],
            scheduled_at=_from_iso(row["scheduled_at"]),
            published_at=_from_iso(row["published_at"]),
            reject_reason=row["reject_reason"],
            publish_target=publish_target,
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )

    def _row_to_media(self, row: sqlite3.Row) -> MediaItem:
        raw_kind = row["ref_kind"]
        return MediaItem.from_ref(
            content_type=ContentType(row["content_type"]),
            order_index=row["order_index"],
            file_ref=row["file_ref"],
            ref_kind=RefKind(raw_kind) if raw_kind else None,
            caption=row["caption"],
        )

    def insert_submission(self, submission: Submission) -> Submission:
        now = utcnow()
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO submissions (
                    source, status, author_platform_user_id, author_display_name,
                    author_username, author_discord_profile_url, want_anonymous,
                    text, is_admin_post, guild_id, source_chat_id,
                    source_message_id, scheduled_at, published_at, reject_reason,
                    publish_target, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission.source.value,
                    submission.status.value,
                    submission.author_platform_user_id,
                    submission.author_display_name,
                    submission.author_username,
                    submission.author_discord_profile_url,
                    None
                    if submission.want_anonymous is None
                    else int(submission.want_anonymous),
                    submission.text,
                    int(submission.is_admin_post),
                    submission.guild_id,
                    submission.source_chat_id,
                    submission.source_message_id,
                    _to_iso(submission.scheduled_at),
                    _to_iso(submission.published_at),
                    submission.reject_reason,
                    submission.publish_target.value,
                    _to_iso(submission.created_at or now),
                    _to_iso(submission.updated_at or now),
                ),
            )
            submission_id = int(cur.lastrowid or 0)
        if submission.media:
            self.replace_media(submission_id, submission.media)
        stored = self.get_submission(submission_id)
        assert stored is not None
        return stored

    def update_submission(self, submission_id: int, **fields: object) -> None:
        unknown = set(fields) - set(_SUBMISSION_UPDATABLE)
        if unknown:
            raise ValueError(f"РќРµРёР·РІРµСЃС‚РЅС‹Рµ РїРѕР»СЏ Р·Р°СЏРІРєРё: {sorted(unknown)}")
        if not fields:
            return

        values: list[object] = []
        assignments: list[str] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if isinstance(value, SubmissionStatus):
                values.append(value.value)
            elif isinstance(value, PublishTarget):
                values.append(value.value)
            elif isinstance(value, datetime):
                values.append(_to_iso(value))
            elif isinstance(value, bool):
                values.append(int(value))
            else:
                values.append(value)
        assignments.append("updated_at = ?")
        values.append(_to_iso(utcnow()))
        values.append(submission_id)

        with self._tx() as conn:
            conn.execute(
                f"UPDATE submissions SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def delete_submission(self, submission_id: int) -> bool:
        """Hard-delete a submission and cascaded media/moderation refs."""
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM submissions WHERE id = ?", (submission_id,)
            )
            return cur.rowcount > 0

    def get_submission(self, submission_id: int) -> Submission | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()
            if row is None:
                return None
            submission = self._row_to_submission(row)
            media_rows = conn.execute(
                """
                SELECT * FROM submission_media
                WHERE submission_id = ? ORDER BY order_index
                """,
                (submission_id,),
            ).fetchall()
        submission.media = [self._row_to_media(r) for r in media_rows]
        return submission

    def list_submissions(
        self,
        *,
        status: SubmissionStatus | None = None,
        source: Source | None = None,
        author_platform_user_id: str | None = None,
        limit: int = 50,
        order_by: str = "id",
    ) -> list[Submission]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if source is not None:
            clauses.append("source = ?")
            params.append(source.value)
        if author_platform_user_id is not None:
            clauses.append("author_platform_user_id = ?")
            params.append(author_platform_user_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Whitelist ORDER BY to avoid SQL injection via kwargs.
        order_sql = {
            "id": "id ASC",
            "id_desc": "id DESC",
            "scheduled_at": (
                "CASE WHEN scheduled_at IS NULL THEN 1 ELSE 0 END, "
                "scheduled_at ASC, id ASC"
            ),
            "created_at": "created_at ASC, id ASC",
        }.get(order_by, "id ASC")
        params.append(limit)

        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT id FROM submissions {where} ORDER BY {order_sql} LIMIT ?",
                params,
            ).fetchall()
        result: list[Submission] = []
        for row in rows:
            submission = self.get_submission(row["id"])
            if submission is not None:
                result.append(submission)
        return result

    # --- media ---------------------------------------------------------------

    def replace_media(self, submission_id: int, items: list[MediaItem]) -> None:
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM submission_media WHERE submission_id = ?",
                (submission_id,),
            )
            conn.executemany(
                """
                INSERT INTO submission_media (
                    submission_id, order_index, content_type, file_ref,
                    ref_kind, caption
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        submission_id,
                        item.order_index,
                        item.content_type.value,
                        item.file_ref,
                        item.ref_kind.value if item.ref_kind else None,
                        item.caption,
                    )
                    for item in items
                ],
            )

    def append_media(self, submission_id: int, items: list[MediaItem]) -> None:
        """Add media keeping album order; order_index=0 means "next free slot"."""
        if not items:
            return
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(order_index), -1) AS max_index
                FROM submission_media WHERE submission_id = ?
                """,
                (submission_id,),
            ).fetchone()
            next_index = int(row["max_index"]) + 1
            payload: list[tuple[object, ...]] = []
            for item in items:
                order_index = (
                    item.order_index if item.order_index > 0 else next_index
                )
                next_index = max(next_index, order_index) + 1
                payload.append(
                    (
                        submission_id,
                        order_index,
                        item.content_type.value,
                        item.file_ref,
                        item.ref_kind.value if item.ref_kind else None,
                        item.caption,
                    )
                )
            conn.executemany(
                """
                INSERT OR REPLACE INTO submission_media (
                    submission_id, order_index, content_type, file_ref,
                    ref_kind, caption
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

    def get_media(self, submission_id: int) -> list[MediaItem]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM submission_media
                WHERE submission_id = ? ORDER BY order_index
                """,
                (submission_id,),
            ).fetchall()
        return [self._row_to_media(row) for row in rows]

    # --- moderation refs -----------------------------------------------------

    def save_moderation_ref(self, ref: ModerationRef) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO moderation_refs (
                    submission_id, platform, target_id, message_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ref.submission_id,
                    Platform(ref.platform).value,
                    ref.target_id,
                    ref.message_id,
                    _to_iso(utcnow()),
                ),
            )

    def get_moderation_refs(
        self, submission_id: int, *, platform: Platform | None = None
    ) -> list[ModerationRef]:
        query = "SELECT * FROM moderation_refs WHERE submission_id = ?"
        params: list[object] = [submission_id]
        if platform is not None:
            query += " AND platform = ?"
            params.append(Platform(platform).value)
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            ModerationRef(
                submission_id=row["submission_id"],
                platform=Platform(row["platform"]),
                target_id=row["target_id"],
                message_id=row["message_id"],
            )
            for row in rows
        ]

    # --- admins --------------------------------------------------------------

    def upsert_admin(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        added_by: str | None = None,
    ) -> Admin:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO admins (
                    platform, platform_user_id, added_by, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(platform, platform_user_id) DO NOTHING
                """,
                (
                    Platform(platform).value,
                    platform_user_id,
                    added_by,
                    _to_iso(utcnow()),
                ),
            )
        admin = self.get_admin(platform, platform_user_id)
        assert admin is not None
        return admin

    def delete_admin(self, platform: Platform, platform_user_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                """
                DELETE FROM admins
                WHERE platform = ? AND platform_user_id = ?
                """,
                (Platform(platform).value, platform_user_id),
            )
        return cur.rowcount > 0

    def get_admin(
        self, platform: Platform, platform_user_id: str
    ) -> Admin | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM admins
                WHERE platform = ? AND platform_user_id = ?
                """,
                (Platform(platform).value, platform_user_id),
            ).fetchone()
        if row is None:
            return None
        return Admin(
            platform=Platform(row["platform"]),
            platform_user_id=row["platform_user_id"],
            added_by=row["added_by"],
            created_at=_from_iso(row["created_at"]),
        )

    def list_admins(self, *, platform: Platform | None = None) -> list[Admin]:
        query = "SELECT * FROM admins"
        params: list[object] = []
        if platform is not None:
            query += " WHERE platform = ?"
            params.append(Platform(platform).value)
        query += " ORDER BY platform, platform_user_id"
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            Admin(
                platform=Platform(row["platform"]),
                platform_user_id=row["platform_user_id"],
                added_by=row["added_by"],
                created_at=_from_iso(row["created_at"]),
            )
            for row in rows
        ]

    # --- guild configs -------------------------------------------------------

    def get_guild_config(self, guild_id: str) -> GuildConfig | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        if row is None:
            return None
        return GuildConfig(
            guild_id=row["guild_id"],
            suggest_channel_id=row["suggest_channel_id"],
            mod_channel_id=row["mod_channel_id"],
            publish_channel_id=(
                row["publish_channel_id"]
                if "publish_channel_id" in row.keys()
                else None
            ),
            propose_role_ids=_load_id_list(row["propose_role_ids"]),
            mod_role_ids=_load_id_list(row["mod_role_ids"]),
            rate_limit_enabled=bool(row["rate_limit_enabled"]),
            rate_limit_count=row["rate_limit_count"],
            rate_limit_window_sec=row["rate_limit_window_sec"],
        )

    def upsert_guild_config(self, config: GuildConfig) -> GuildConfig:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO guild_configs (
                    guild_id, suggest_channel_id, mod_channel_id,
                    publish_channel_id, propose_role_ids, mod_role_ids,
                    rate_limit_enabled, rate_limit_count, rate_limit_window_sec,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    suggest_channel_id = excluded.suggest_channel_id,
                    mod_channel_id = excluded.mod_channel_id,
                    publish_channel_id = excluded.publish_channel_id,
                    propose_role_ids = excluded.propose_role_ids,
                    mod_role_ids = excluded.mod_role_ids,
                    rate_limit_enabled = excluded.rate_limit_enabled,
                    rate_limit_count = excluded.rate_limit_count,
                    rate_limit_window_sec = excluded.rate_limit_window_sec,
                    updated_at = excluded.updated_at
                """,
                (
                    config.guild_id,
                    config.suggest_channel_id,
                    config.mod_channel_id,
                    config.publish_channel_id,
                    json.dumps([str(x) for x in config.propose_role_ids]),
                    json.dumps([str(x) for x in config.mod_role_ids]),
                    int(config.rate_limit_enabled),
                    config.rate_limit_count,
                    config.rate_limit_window_sec,
                    _to_iso(utcnow()),
                ),
            )
        stored = self.get_guild_config(config.guild_id)
        assert stored is not None
        return stored

    def list_guild_configs(self) -> list[GuildConfig]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM guild_configs ORDER BY guild_id"
            ).fetchall()
        return [
            GuildConfig(
                guild_id=row["guild_id"],
                suggest_channel_id=row["suggest_channel_id"],
                mod_channel_id=row["mod_channel_id"],
                publish_channel_id=(
                    row["publish_channel_id"]
                    if "publish_channel_id" in row.keys()
                    else None
                ),
                propose_role_ids=_load_id_list(row["propose_role_ids"]),
                mod_role_ids=_load_id_list(row["mod_role_ids"]),
                rate_limit_enabled=bool(row["rate_limit_enabled"]),
                rate_limit_count=row["rate_limit_count"],
                rate_limit_window_sec=row["rate_limit_window_sec"],
            )
            for row in rows
        ]

    # --- mirror links --------------------------------------------------------

    def _row_to_mirror_link(self, row: sqlite3.Row) -> MirrorLink:
        return MirrorLink(
            id=row["id"],
            origin=Platform(row["origin"]),
            kind=MirrorKind(row["kind"]),
            tg_chat_id=row["tg_chat_id"],
            tg_message_id=row["tg_message_id"],
            ds_guild_id=row["ds_guild_id"],
            ds_channel_id=row["ds_channel_id"],
            ds_message_id=row["ds_message_id"],
            submission_id=row["submission_id"],
            created_at=_from_iso(row["created_at"]),
        )

    def insert_mirror_link(self, link: MirrorLink) -> MirrorLink:
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO mirror_links (
                    origin, kind, tg_chat_id, tg_message_id,
                    ds_guild_id, ds_channel_id, ds_message_id,
                    submission_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link.origin.value,
                    link.kind.value,
                    link.tg_chat_id,
                    link.tg_message_id,
                    link.ds_guild_id,
                    link.ds_channel_id,
                    link.ds_message_id,
                    link.submission_id,
                    _to_iso(link.created_at or utcnow()),
                ),
            )
            link_id = int(cur.lastrowid or 0)
        stored = self.get_mirror_link(link_id)
        assert stored is not None
        return stored

    def get_mirror_link(self, link_id: int) -> MirrorLink | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM mirror_links WHERE id = ?", (link_id,)
            ).fetchone()
        return self._row_to_mirror_link(row) if row else None

    def find_mirror_by_tg(
        self, tg_chat_id: str, tg_message_id: str
    ) -> MirrorLink | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM mirror_links
                WHERE tg_chat_id = ? AND tg_message_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (str(tg_chat_id), str(tg_message_id)),
            ).fetchone()
        return self._row_to_mirror_link(row) if row else None

    def find_mirror_by_ds(
        self, ds_channel_id: str, ds_message_id: str
    ) -> MirrorLink | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM mirror_links
                WHERE ds_channel_id = ? AND ds_message_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (str(ds_channel_id), str(ds_message_id)),
            ).fetchone()
        return self._row_to_mirror_link(row) if row else None

    def update_mirror_link(self, link_id: int, **fields: object) -> None:
        allowed = {
            "tg_chat_id",
            "tg_message_id",
            "ds_guild_id",
            "ds_channel_id",
            "ds_message_id",
            "submission_id",
            "kind",
            "origin",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"РќРµРёР·РІРµСЃС‚РЅС‹Рµ РїРѕР»СЏ mirror_links: {sorted(unknown)}")
        if not fields:
            return
        values: list[object] = []
        assignments: list[str] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if isinstance(value, (Platform, MirrorKind)):
                values.append(value.value)
            else:
                values.append(value)
        values.append(link_id)
        with self._tx() as conn:
            conn.execute(
                f"UPDATE mirror_links SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def delete_mirror_link(self, link_id: int) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM mirror_links WHERE id = ?", (link_id,)
            )
        return cur.rowcount > 0

    # --- blacklist -----------------------------------------------------------

    def upsert_blacklist(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        reason: str | None = None,
    ) -> BlacklistEntry:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO blacklist (
                    platform, platform_user_id, reason, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(platform, platform_user_id) DO UPDATE SET
                    reason = excluded.reason
                """,
                (
                    Platform(platform).value,
                    platform_user_id,
                    reason,
                    _to_iso(utcnow()),
                ),
            )
        entry = self.get_blacklist_entry(platform, platform_user_id)
        assert entry is not None
        return entry

    def delete_blacklist(
        self, platform: Platform, platform_user_id: str
    ) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                """
                DELETE FROM blacklist
                WHERE platform = ? AND platform_user_id = ?
                """,
                (Platform(platform).value, platform_user_id),
            )
        return cur.rowcount > 0

    def get_blacklist_entry(
        self, platform: Platform, platform_user_id: str
    ) -> BlacklistEntry | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM blacklist
                WHERE platform = ? AND platform_user_id = ?
                """,
                (Platform(platform).value, platform_user_id),
            ).fetchone()
        if row is None:
            return None
        return BlacklistEntry(
            platform=Platform(row["platform"]),
            platform_user_id=row["platform_user_id"],
            reason=row["reason"],
            created_at=_from_iso(row["created_at"]),
        )

    def list_blacklist(
        self, *, platform: Platform | None = None
    ) -> list[BlacklistEntry]:
        query = "SELECT * FROM blacklist"
        params: list[object] = []
        if platform is not None:
            query += " WHERE platform = ?"
            params.append(Platform(platform).value)
        query += " ORDER BY platform, platform_user_id"
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            BlacklistEntry(
                platform=Platform(row["platform"]),
                platform_user_id=row["platform_user_id"],
                reason=row["reason"],
                created_at=_from_iso(row["created_at"]),
            )
            for row in rows
        ]

    # --- antiflood -----------------------------------------------------------

    def bump_antiflood(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        now: float,
        window_sec: int,
    ) -> int:
        """Register a hit and return the hit count inside the current window."""
        with self._tx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT window_start, count FROM antiflood_hits
                WHERE platform = ? AND platform_user_id = ?
                """,
                (Platform(platform).value, platform_user_id),
            ).fetchone()
            if row is None or now - float(row["window_start"]) >= window_sec:
                window_start = now
                count = 1
            else:
                window_start = float(row["window_start"])
                count = int(row["count"]) + 1
            conn.execute(
                """
                INSERT OR REPLACE INTO antiflood_hits (
                    platform, platform_user_id, window_start, count
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    Platform(platform).value,
                    platform_user_id,
                    window_start,
                    count,
                ),
            )
        return count

    def reset_antiflood(
        self, platform: Platform, platform_user_id: str
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                DELETE FROM antiflood_hits
                WHERE platform = ? AND platform_user_id = ?
                """,
                (Platform(platform).value, platform_user_id),
            )

    # --- temporary role pass requests ----------------------------------------

    def _row_to_pass_request(self, row: sqlite3.Row) -> PassRequest:
        return PassRequest(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            display_name=row["display_name"],
            username=row["username"],
            status=PassRequestStatus(row["status"]),
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
            decided_at=_from_iso(row["decided_at"]),
            decided_by=row["decided_by"],
            expires_at=_from_iso(row["expires_at"]),
            cooldown_until=_from_iso(row["cooldown_until"]),
            mod_channel_id=row["mod_channel_id"],
            mod_message_id=row["mod_message_id"],
        )

    def insert_pass_request(self, request: PassRequest) -> PassRequest | None:
        """Insert a pending request. Returns None if one is already pending."""
        now = utcnow()
        created = request.created_at or now
        updated = request.updated_at or now
        with self._tx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT id FROM pass_requests
                WHERE guild_id = ? AND user_id = ? AND status = ?
                """,
                (
                    request.guild_id,
                    request.user_id,
                    PassRequestStatus.pending.value,
                ),
            ).fetchone()
            if existing is not None:
                return None
            cur = conn.execute(
                """
                INSERT INTO pass_requests (
                    guild_id, user_id, display_name, username, status,
                    created_at, updated_at, decided_at, decided_by,
                    expires_at, cooldown_until, mod_channel_id, mod_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.guild_id,
                    request.user_id,
                    request.display_name,
                    request.username,
                    request.status.value,
                    _to_iso(created),
                    _to_iso(updated),
                    _to_iso(request.decided_at),
                    request.decided_by,
                    _to_iso(request.expires_at),
                    _to_iso(request.cooldown_until),
                    request.mod_channel_id,
                    request.mod_message_id,
                ),
            )
            request.id = int(cur.lastrowid)
            request.created_at = created
            request.updated_at = updated
        return request

    def get_pass_request(self, request_id: int) -> PassRequest | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM pass_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return self._row_to_pass_request(row) if row else None

    def get_pending_pass(
        self, guild_id: str, user_id: str
    ) -> PassRequest | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM pass_requests
                WHERE guild_id = ? AND user_id = ? AND status = ?
                ORDER BY id DESC LIMIT 1
                """,
                (guild_id, user_id, PassRequestStatus.pending.value),
            ).fetchone()
        return self._row_to_pass_request(row) if row else None

    def get_active_pass(
        self, guild_id: str, user_id: str, *, now: datetime
    ) -> PassRequest | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM pass_requests
                WHERE guild_id = ? AND user_id = ? AND status = ?
                  AND expires_at IS NOT NULL AND expires_at > ?
                ORDER BY id DESC LIMIT 1
                """,
                (
                    guild_id,
                    user_id,
                    PassRequestStatus.approved.value,
                    _to_iso(now),
                ),
            ).fetchone()
        return self._row_to_pass_request(row) if row else None

    def get_reject_cooldown(
        self, guild_id: str, user_id: str, *, now: datetime
    ) -> PassRequest | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM pass_requests
                WHERE guild_id = ? AND user_id = ? AND status = ?
                  AND cooldown_until IS NOT NULL AND cooldown_until > ?
                ORDER BY id DESC LIMIT 1
                """,
                (
                    guild_id,
                    user_id,
                    PassRequestStatus.rejected.value,
                    _to_iso(now),
                ),
            ).fetchone()
        return self._row_to_pass_request(row) if row else None

    def list_pass_requests(
        self,
        *,
        status: PassRequestStatus | None = None,
        limit: int = 200,
    ) -> list[PassRequest]:
        query = "SELECT * FROM pass_requests"
        params: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status.value)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_pass_request(row) for row in rows]

    def list_due_pass_grants(self, *, now: datetime) -> list[PassRequest]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pass_requests
                WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY id ASC
                """,
                (PassRequestStatus.approved.value, _to_iso(now)),
            ).fetchall()
        return [self._row_to_pass_request(row) for row in rows]

    def update_pass_request(self, request: PassRequest) -> PassRequest:
        if request.id is None:
            raise ValueError("pass request id is required")
        request.updated_at = utcnow()
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE pass_requests SET
                    display_name = ?, username = ?, status = ?,
                    updated_at = ?, decided_at = ?, decided_by = ?,
                    expires_at = ?, cooldown_until = ?,
                    mod_channel_id = ?, mod_message_id = ?
                WHERE id = ?
                """,
                (
                    request.display_name,
                    request.username,
                    request.status.value,
                    _to_iso(request.updated_at),
                    _to_iso(request.decided_at),
                    request.decided_by,
                    _to_iso(request.expires_at),
                    _to_iso(request.cooldown_until),
                    request.mod_channel_id,
                    request.mod_message_id,
                    request.id,
                ),
            )
        return request

    def claim_pass_decision(
        self,
        request_id: int,
        *,
        expected_status: PassRequestStatus,
        new_status: PassRequestStatus,
        decided_by: str,
        now: datetime,
        expires_at: datetime | None = None,
        cooldown_until: datetime | None = None,
    ) -> PassRequest | None:
        """Atomically move a request out of ``expected_status``."""
        with self._tx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE pass_requests SET
                    status = ?, updated_at = ?, decided_at = ?, decided_by = ?,
                    expires_at = ?, cooldown_until = ?
                WHERE id = ? AND status = ?
                """,
                (
                    new_status.value,
                    _to_iso(now),
                    _to_iso(now),
                    decided_by,
                    _to_iso(expires_at),
                    _to_iso(cooldown_until),
                    request_id,
                    expected_status.value,
                ),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM pass_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return self._row_to_pass_request(row) if row else None

    def bump_pass_antiflood(
        self,
        guild_id: str,
        user_id: str,
        *,
        now: float,
        window_sec: int,
    ) -> tuple[int, float, float | None]:
        """Hit counter for /prohodka. Returns (count, last_hit, strike_until)."""
        with self._tx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT window_start, count, last_hit, strike_until
                FROM pass_antiflood
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
            strike_until = (
                float(row["strike_until"])
                if row is not None and row["strike_until"] is not None
                else None
            )
            if row is None or now - float(row["window_start"]) >= window_sec:
                window_start = now
                count = 1
            else:
                window_start = float(row["window_start"])
                count = int(row["count"]) + 1
            conn.execute(
                """
                INSERT OR REPLACE INTO pass_antiflood (
                    guild_id, user_id, window_start, count, last_hit,
                    strike_until
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, window_start, count, now, strike_until),
            )
        return count, now, strike_until

    def peek_pass_antiflood(
        self, guild_id: str, user_id: str
    ) -> tuple[float, float | None]:
        """Return (last_hit, strike_until); last_hit is 0 if unseen."""
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT last_hit, strike_until FROM pass_antiflood
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
        if row is None:
            return 0.0, None
        strike = (
            float(row["strike_until"])
            if row["strike_until"] is not None
            else None
        )
        return float(row["last_hit"] or 0), strike

    def set_pass_strike(
        self, guild_id: str, user_id: str, *, strike_until: float
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE pass_antiflood SET strike_until = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (strike_until, guild_id, user_id),
            )

    # --- settings ------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None or row["value"] is None:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str | None) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_int_setting(self, key: str, default: int) -> int:
        raw = self.get_setting(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def delete_setting(self, key: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def _load_id_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]
