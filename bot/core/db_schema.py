"""Bridge SQLite DDL (split from db.py for maintainability)."""

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
    admin_role_ids TEXT NOT NULL DEFAULT '[]',
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
