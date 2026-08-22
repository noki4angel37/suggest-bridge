# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |

## Reporting a vulnerability

Please **do not** open public GitHub issues for security problems.

1. Open a [GitHub Security Advisory](https://github.com/noki4angel37/suggest-bridge/security/advisories/new) (preferred), or
2. Email the maintainers if you cannot use GitHub (add your contact in the repo settings).

We aim to respond within 7 days.

## Secrets

- Never commit `.env`, `local.env`, or database files with live tokens.
- Rotate `BOT_TOKEN` and `DISCORD_TOKEN` if they were exposed.
- Run the bot on a host you control; it stores moderation data in SQLite locally.

## Permissions

The Discord bot requests elevated permissions to delete user submissions from public channels and manage moderation cards. Review the invite URL and grant only what you need.
