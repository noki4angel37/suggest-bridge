# Suggest Bridge (English)

**Suggest Bridge** is a self-hosted **Telegram ↔ Discord community platform**: suggest submissions, cross-platform bridge, and a shared moderation queue are equal modules on one core. Also: pass requests, casino mini-games, Multi-PC host.

Members do not need tokens, servers, or setup — just DM the bot or use a command on Discord (see the **suggest** module below).

| | |
|---|---|
| Repository | [github.com/noki4angel37/suggest-bridge](https://github.com/noki4angel37/suggest-bridge) |
| Discord | [discord.gg/F3fBdeTx94](https://discord.gg/F3fBdeTx94) |
| License | MIT |
| Python | 3.11+ |
| Stack | aiogram 3, discord.py, SQLite (WAL) |

> Russian documentation: [[Home]]. Admin quick start: [[Быстрый-старт]] or [README](https://github.com/noki4angel37/suggest-bridge/blob/main/README.en.md).

## Platform modules

| Module | Purpose |
|--------|---------|
| [[Модерация]] / suggest | Submissions → queue → publish |
| [[Зеркало-ленты]] | TG↔DS bridge, dual-publish |
| [[Проходка]] | Temporary roles via moderation |
| Custom | [[Модули]], [[Добавить-модуль]] — `SB_MODULES` |

## How the suggest module works

1. **You submit** text or media (up to 400 characters in the caption).
2. **Moderators review** the submission in a queue — approve, reject, or ask for clarification.
3. **Approved posts** go to the channel or `#предложка` feed — with your name or anonymously.

## For community members

| Page | Purpose |
|------|---------|
| [[How-to-submit-en]] | Step-by-step for Telegram and Discord |
| [[Member-FAQ-en]] | Common questions |
| [[Discord-сервер]] | Community, news, support |

## For administrators

1. Configure tokens — [[Конфигурация]], [[Telegram]], [[Discord]]
2. Run the bot — [[Быстрый-старт]] / [[Деплой]]
3. Reference — [[Команды]], [[Модерация]], [[Модули]]
4. Incidents — [[Устранение-неисправностей]]

## Repository

| Document | Purpose |
|----------|------------|
| [README.en.md](https://github.com/noki4angel37/suggest-bridge/blob/main/README.en.md) | Overview |
| [SETUP.md](https://github.com/noki4angel37/suggest-bridge/blob/main/SETUP.md) | Install |
| [Issues](https://github.com/noki4angel37/suggest-bridge/issues) | Bugs and ideas |
