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

- Dual-publish vs channel mirror race (mirror stub before Discord side)
- Scheduler republish loop when `mark_published` failed after successful publish
- Telegram-only mode no longer wires Discord publisher on `both` target
- Album rollback deletes all Telegram message ids
- CAS status updates on approve/reject/publish; SQLite `timeout=30`
- Discord retry publish for approved-but-unpublished submissions
- Mirror text truncated to Discord 2000 / Telegram 1024
- Guild decorate/setup: break-glass overwrites for owner and invoker

### Added

- GitHub Pages site ([noki4angel37.github.io/suggest-bridge](https://noki4angel37.github.io/suggest-bridge/)): landing, user guide, FAQ, Discord community page (Jekyll just-the-docs)
- README: subscriber section above admin quick start; Discord badge and site link; documentation table
- Public Discord invite in README, SETUP, CONTRIBUTING, wiki sidebar/footer
- `bot/core/safe_fetch.py`, health endpoint (`HEALTH_PORT`), CI ruff lint
- Docker runs as non-root user; GitHub Actions pinned by commit SHA
- Deploy scripts `scripts/deploy/prepare-env.*`, multi-page wiki source [`docs/wiki/`](docs/wiki/) (publish notes [docs/WIKI.md](docs/WIKI.md))
- Discord invite permissions documented as **268561488** (adds Manage Roles for `/setup_suggest`)

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
