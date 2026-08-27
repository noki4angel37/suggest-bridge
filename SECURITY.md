# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |

## Reporting a vulnerability

Please **do not** open public GitHub issues for security problems.

1. Open a [GitHub Security Advisory](https://github.com/noki4angel37/suggest-bridge/security/advisories/new) (preferred), or
2. Email via [GitHub Security Advisory](https://github.com/noki4angel37/suggest-bridge/security/advisories/new) private report.

We aim to respond within 7 days.

## Secrets

- Never commit `.env`, `local.env`, or database files with live tokens.
- Rotate `BOT_TOKEN` and `DISCORD_TOKEN` if they were exposed.
- Run the bot on a host you control; it stores moderation data in SQLite locally.

## Permissions

Use OAuth2 permissions integer **268561488** (includes Manage Messages and Manage Roles for `/setup_suggest`). Do **not** grant Administrator. Bit breakdown: project wiki → Discord.

## Operator defaults

- Do not expose `HEALTH_PORT` (binds `0.0.0.0`) to the public internet without need; prefer `127.0.0.1` publish or leave unset.
- Syncthing peers with write on `HOST_SYNC_DIR` are high trust; HMAC does not replace device trust. Rotate `HOST_SYNC_SECRET` on all PCs if a peer is compromised.
- Full operator security notes: [wiki Безопасность](https://github.com/noki4angel37/suggest-bridge/wiki/Безопасность).
