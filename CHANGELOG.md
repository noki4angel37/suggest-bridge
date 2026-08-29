# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Discord `/host`: only bot admins/owner; guild moderators get read-only sticky panel
- HMAC-signed host-sync commands, acks, and registry (`HOST_SYNC_SECRET`)
- SSRF hardening: CDN/TG allowlist for attachment downloads; URL redaction in logs
- Host accept/reject authorized by holder/offer recipient; `stop_local` targets actor PC

### Fixed

- Discord `/` picker no longer shows each slash command twice: publish **global only** and clear per-guild command buckets on startup (guild+global duplicates / client cache)
- `/decorate_server` announce-locks leftover publish aliases (`#посты-опубликовано` / `#посты-опубликованно`); members can read the feed but cannot write
- Telegram network errors no longer stop the whole process (Discord stays connected; polling retries)
- Windows console logging uses UTF-8 with replacement so emoji in channel names does not crash the logger
- Dual-publish vs channel mirror race (mirror stub before Discord side)
- Scheduler republish loop when `mark_published` failed after successful publish
- Telegram-only mode no longer wires Discord publisher on `both` target
- Album rollback deletes all Telegram message ids
- CAS status updates on approve/reject/publish; SQLite `timeout=30`
- Discord retry publish for approved-but-unpublished submissions
- Mirror text truncated to Discord 2000 / Telegram 1024
- Guild decorate/setup: break-glass overwrites for owner and invoker

### Changed

- Product positioning: Suggest Bridge is a **modular platform** (suggest, bridge, moderation are equal modules), not «suggest bot only»
- Discord slash command tree can attach a Russian translator (`locale_str(..., ru=...)`) so localized names work when commands use it
- Documentation consolidated in GitHub Wiki; GitHub Pages removed
- Subscriber pages moved to wiki: Как-отправить-заявку, FAQ-подписчиков, Discord-сервер
- Wiki: subscriber pages (rules, UI examples, news), admin onboarding (first launch, Discord layout, run modes), operator deep-dives (mirror, anti-flood, roles, scheduled publish, migration, host requirements), English member docs, privacy and wiki contribution guides
- README: subscriber section above admin quick start; Discord badge and wiki link; documentation table
- Public Discord invite in README, SETUP, CONTRIBUTING, wiki sidebar/footer
- Wiki subscriber pages and expanded Home/sidebar navigation
- `bot/core/safe_fetch.py`, health endpoint (`HEALTH_PORT`), CI ruff lint
- Docker runs as non-root user; GitHub Actions pinned by commit SHA
- Deploy scripts `scripts/deploy/prepare-env.*`, multi-page wiki source [`docs/wiki/`](docs/wiki/) (publish notes [docs/WIKI.md](docs/WIKI.md))
- Discord invite permissions documented as **268561488** (adds Manage Roles for `/setup_suggest`)

### Added

- **Plugin API:** load third-party modules via `SB_MODULES` (`bot/core/modules.py`, `module_loader.py`); hooks `setup`, `setup_telegram`, `setup_discord`, `teardown`
- Plugin API hardening: repo-root path resolution, dedup specs, `setup_discord` once per process, LIFO `teardown` with Discord context, `/healthz` `checks.modules`, `SB_MODULES_STRICT`, `python -m bot.core.module_loader` validate CLI
- Example module: `examples/sample_module/` (`/sample_ping` on TG and Discord when enabled)
- Wiki: Модули, Добавить-модуль; README repositioned as modular TG↔DS community platform
- Discord pass rooms: `/prohodka` requests a temporary role via suggest moderation; `/setup_pass` creates or locks channels behind that role (repeatable, not theme-specific)

## [0.1.0] - 2026-08-22

### Added

- Public release of **Suggest Bridge** — self-hosted Telegram ↔ Discord suggest bot
- Run modes: Telegram-only, Discord-only, or both
- Docker Compose and systemd unit examples
- Windows agent (`install-agent.ps1`) and zip distribution via `/download`
- Configurable channel names, hashtag, and anonymous label via `.env`
- Moderation queue, scheduled publish, mirror feed, anti-flood, `/setup_suggest`
- Multi-PC `/host` handover for admin workstations
- Bilingual README (RU + EN), SETUP guide, SECURITY policy
- CI: pytest on Python 3.11 and 3.12

[0.1.0]: https://github.com/noki4angel37/suggest-bridge/releases/tag/v0.1.0
