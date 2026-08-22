<p align="center">
  <img src="docs/assets/logo.png" alt="Suggest Bridge" width="128" />
</p>

# Suggest Bridge

[![CI](https://github.com/noki4angel/suggest-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/noki4angel/suggest-bridge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Self-hosted community suggest bot** for a Telegram channel and a Discord server: submissions, moderation, publishing, feed mirroring, anti-flood.

Русский: [README.md](README.md)

## Who it is for

- Admins of **Russian-speaking** Telegram channels and Discord servers (UI is RU; docs are bilingual)
- Communities that want **their own** suggest box without SaaS lock-in
- One instance = **one** community (one channel + one server)

## Features

| Feature | Description |
|---------|-------------|
| Submissions | Telegram DMs and Discord (`/suggest`, suggest channel) |
| Moderation | Queue, approve, reject, reply to author, scheduled publish |
| Publishing | Telegram channel and/or Discord feed (dual-publish) |
| Anonymity | Authors can hide their name |
| Mirror | TG ↔ Discord feed sync |
| Server setup | `/setup_suggest`, `/decorate_server` |
| Multi-PC | `/host` + Windows agent for admin workstations |
| Run modes | Telegram-only, Discord-only, or both |

## Architecture

```mermaid
flowchart LR
  subgraph input [Submissions]
    TGUser[Telegram DM]
    DSUser[Discord channel]
  end
  subgraph core [Suggest Bridge]
    Queue[Moderation queue]
    Mod[Mod cards]
    Router[PublishRouter]
  end
  subgraph output [Publish]
    TGCh[Telegram channel]
    DSCh[Discord feed]
  end
  TGUser --> Queue
  DSUser --> Queue
  Queue --> Mod
  Mod --> Router
  Router --> TGCh
  Router --> DSCh
```

Mock UI examples: [docs/assets/screenshots-mockup.md](docs/assets/screenshots-mockup.md)

## Quick start

### Docker

```bash
git clone https://github.com/noki4angel/suggest-bridge.git
cd suggest-bridge
cp .env.example .env
# fill BOT_TOKEN, DISCORD_TOKEN, ADMIN_IDS, CHANNEL_ID
docker compose up -d
```

See [SETUP.md](SETUP.md) for Discord Developer Portal and BotFather steps.

## Configuration

Copy [`.env.example`](.env.example). Channel names and hashtag default to Russian (`#предложка`) but are overridable via env.

## Support

- [GitHub Issues](https://github.com/noki4angel/suggest-bridge/issues)
- Discord support server — add invite link when available

## License

[MIT](LICENSE)
