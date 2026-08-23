<p align="center">
  <img src="docs/assets/logo.png" alt="Suggest Bridge" width="128" />
</p>

# Suggest Bridge

[![CI](https://github.com/noki4angel37/suggest-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/noki4angel37/suggest-bridge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Discord](https://img.shields.io/badge/Discord-сервер-5865F2?logo=discord&logoColor=white)](https://discord.gg/F3fBdeTx94)
[![Сайт](https://img.shields.io/badge/сайт-github.io-24292f)](https://noki4angel37.github.io/suggest-bridge/)

**Self-hosted бот предложки** для Telegram-канала и Discord-сервера: подписчики отправляют идеи, модераторы проверяют, одобренное публикуется в канале или ленте.

English: [README.en.md](README.en.md) · **Сайт для подписчиков:** [noki4angel37.github.io/suggest-bridge](https://noki4angel37.github.io/suggest-bridge/)

---

## Что это

**Suggest Bridge** связывает Telegram и Discord одной очередью модерации. Подписчик пишет боту или использует `/suggest` на сервере — модераторы видят карточку заявки, одобряют или отклоняют, и пост выходит в Telegram-канал и/или Discord `#предложка`. Можно анонимно, с отложенной публикацией и зеркалом ленты между платформами.

| Кто | Что делает |
|-----|------------|
| **Подписчик** | Отправляет текст (≤400 символов) и медиа через Telegram или Discord |
| **Модератор** | Одобряет, отклоняет, отвечает автору, планирует время публикации |
| **Администратор** | Разворачивает бота, настраивает токены и каналы |

---

## Для подписчиков

Не нужны токены и установка — только бот в Telegram или сервер в Discord.

1. **Telegram:** личка боту → `/start` → текст → кнопки «анонимно / с именем» → «отправить на модерацию».
2. **Discord:** `/suggest` или сообщение в канал заявок → подтверждение в ЛС бота.

Подробнее на сайте: **[Как отправить заявку](https://noki4angel37.github.io/suggest-bridge/user-guide)** · [FAQ](https://noki4angel37.github.io/suggest-bridge/faq) · **[Discord](https://discord.gg/F3fBdeTx94)**

---

## Для администраторов

### Для кого

- Админы **русскоязычных** Telegram-каналов и Discord-серверов
- Сообщества, которым нужна **своя** предложка без SaaS и без привязки к одному хостингу
- Один экземпляр = **одно** сообщество (канал + сервер)

### Возможности

| Функция | Описание |
|---------|----------|
| Заявки | Telegram ЛС и Discord (`/suggest`, канал заявок) |
| Модерация | Очередь, одобрение, отклонение, ответ автору, отложенная публикация |
| Публикация | Telegram-канал и/или Discord `#предложка` (dual-publish) |
| Анонимность | Автор может скрыть имя |
| Зеркало | Синхронизация ленты TG ↔ Discord |
| Настройка сервера | `/setup_suggest`, `/decorate_server` (категории, ACL, emoji┃имена) |
| Multi-PC | `/host` + Windows-агент для нескольких ПК админов |
| Режимы | Только TG, только Discord, или оба |

### Как это работает

```mermaid
flowchart LR
  subgraph input [Заявки]
    TGUser[Telegram ЛС]
    DSUser[Discord канал]
  end
  subgraph core [Suggest Bridge]
    Queue[Очередь модерации]
    Mod[Карточки модерации]
    Router[PublishRouter]
  end
  subgraph output [Публикация]
    TGCh[Telegram канал]
    DSCh[Discord лента]
  end
  TGUser --> Queue
  DSUser --> Queue
  Queue --> Mod
  Mod --> Router
  Router --> TGCh
  Router --> DSCh
```

Примеры UI (вымышленные данные): [docs/assets/screenshots-mockup.md](docs/assets/screenshots-mockup.md)

### Быстрый старт (5 минут)

#### Docker (Linux / VPS)

```bash
git clone https://github.com/noki4angel37/suggest-bridge.git
cd suggest-bridge
bash scripts/deploy/prepare-env.sh   # или .\scripts\deploy\prepare-env.ps1
# отредактируйте .env — токены BotFather / Discord
docker compose up -d
```

#### Windows

1. Скачайте zip из [Releases](https://github.com/noki4angel37/suggest-bridge/releases)
2. Распакуйте → `Copy-Item .env.example .env` → заполните токены
3. `python -m venv .venv` → `.\.venv\Scripts\pip install -r requirements.txt`
4. `.\.venv\Scripts\python.exe -m bot.main`

### Конфигурация

Скопируйте [`.env.example`](.env.example). Основные переменные:

| Переменная | Назначение |
|------------|------------|
| `BOT_TOKEN` | Telegram BotFather |
| `ADMIN_IDS` | Telegram id админов через запятую |
| `CHANNEL_ID` | Id канала публикации (`-100…`) |
| `DISCORD_TOKEN` | Discord bot token |
| `OWNER_DISCORD_ID` | Супер-админ Discord (обязателен в DS-only) |
| `HOST_SYNC_SECRET` | Multi-PC / агент (один секрет на все ПК) |
| `HEALTH_PORT` | HTTP `/healthz` (Docker: `8080`) |

Имена каналов и хэштег настраиваются (`SUGGEST_HASHTAG`, `SETUP_PUBLISH_CHANNEL_NAME`, …) — по умолчанию русские `#предложка`, `ПРЕДЛОЖКИ`.

---

## Документация

| Документ | Для кого |
|----------|----------|
| **[Сайт](https://noki4angel37.github.io/suggest-bridge/)** | Подписчики: как отправить заявку, FAQ, Discord |
| [SETUP.md](SETUP.md) | Установка с нуля (Docker, Windows, systemd) |
| **[Wiki](https://github.com/noki4angel37/suggest-bridge/wiki)** | Операторы: команды, деплой, инциденты |
| [SECURITY.md](SECURITY.md) | Сообщить об уязвимости |
| [CHANGELOG.md](CHANGELOG.md) | История версий |

---

## Поддержка

- **[Discord](https://discord.gg/F3fBdeTx94)** — вопросы, новости, помощь с установкой
- [GitHub Issues](https://github.com/noki4angel37/suggest-bridge/issues) — баги и идеи (без токенов в описании)

## Лицензия

[MIT](LICENSE) · см. [CHANGELOG](CHANGELOG.md), [SECURITY](SECURITY.md)
