# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
