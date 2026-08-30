# Suggest Bridge (English)

**Suggest Bridge** is an open-source Telegram ↔ Discord **core**. You add features on your machine via `SB_MODULES`; they are not merged into this repository. Bundled examples: suggest, bridge, moderation, and others.

Add your own features: [[Модули]] · [[Добавить-модуль]].

Members do not need tokens, servers, or setup — just DM the bot or use a command on Discord (see the **suggest** example below).

| | |
|---|---|
| Repository | [github.com/noki4angel37/suggest-bridge](https://github.com/noki4angel37/suggest-bridge) |
| Discord | [discord.gg/F3fBdeTx94](https://discord.gg/F3fBdeTx94) |
| License | MIT |
| Python | 3.11+ |
| Stack | aiogram 3, discord.py, SQLite (WAL) |

> Russian documentation: [[Home]]. Admin quick start: [[Быстрый-старт]] or [README](https://github.com/noki4angel37/suggest-bridge/blob/main/README.en.md).

## Core and local features

The core is what this repository ships. Your features stay on your machine: a path or package in `SB_MODULES`, not a merge into this GitHub.

| Page | Purpose |
|------|---------|
| [[Модули]] | How modules and `SB_MODULES` work |
| [[Добавить-модуль]] | How to write a module and load it locally |

## Bundled examples

| Module | Purpose |
|--------|---------|
| [[Модерация]] / suggest | Submissions → queue → publish |
| [[Зеркало-ленты]] | TG↔DS bridge, dual-publish |
| [[Проходка]] | Temporary roles via moderation |
| Custom | local: [[Модули]], [[Добавить-модуль]] — `SB_MODULES` |

## How the suggest module works (example)

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
