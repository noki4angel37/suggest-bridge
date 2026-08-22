# Suggest Bridge — установка с нуля

Self-hosted бот предложки для **одного** Telegram-канала и/или **одного** Discord-сервера.

## Быстрый старт (Docker, рекомендуется)

```bash
git clone https://github.com/noki4angel/suggest-bridge.git
cd suggest-bridge
cp .env.example .env
# заполните токены в .env
docker compose up -d
docker compose logs -f
```

Данные SQLite: `./data/bridge.db`.

## Режимы

| Режим | Переменные |
|-------|------------|
| Telegram + Discord | `BOT_TOKEN`, `DISCORD_TOKEN`, `ADMIN_IDS`, `CHANNEL_ID` |
| Только Telegram | `BOT_TOKEN`, `ADMIN_IDS`, `CHANNEL_ID` (без `DISCORD_TOKEN`) |
| Только Discord | `DISCORD_TOKEN`, `OWNER_DISCORD_ID` (без `BOT_TOKEN`) |

## Discord Developer Portal

1. [discord.com/developers](https://discord.com/developers/applications) → New Application → Bot.
2. **Privileged Gateway Intents**: включите **Message Content** и **Server Members**.
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`, права **126032** (Manage Messages для скрытия заявок).
4. Пригласите бота на сервер по сгенерированной ссылке.
5. На сервере выполните `/setup_suggest` — создаст каналы и роли (имена настраиваются в `.env`).
6. Оформление категорий: `/decorate_server`.

## Telegram BotFather

1. `/newbot` → получите `BOT_TOKEN`.
2. Создайте канал, добавьте бота **администратором** (публикация сообщений).
3. Узнайте `CHANNEL_ID` (формат `-100…`) — через @RawDataBot или API.
4. `ADMIN_IDS` — ваш числовой Telegram user id (Developer mode → Copy User ID).

## Linux (systemd)

```bash
sudo mkdir -p /opt/suggest-bridge /etc/suggest-bridge
sudo cp -r . /opt/suggest-bridge/
python3 -m venv /opt/suggest-bridge/.venv
/opt/suggest-bridge/.venv/bin/pip install -r /opt/suggest-bridge/requirements.txt
sudo cp .env.example /etc/suggest-bridge/env
# отредактируйте /etc/suggest-bridge/env
sudo cp contrib/systemd/suggest-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now suggest-bridge
```

## Windows (zip / агент)

1. Скачайте zip из [Releases](https://github.com/noki4angel/suggest-bridge/releases) или `/download` в боте.
2. Распакуйте, откройте PowerShell в папке:

```powershell
Copy-Item .env.example .env
# заполните .env
.\install-agent.ps1
.\run-agent.ps1
```

Агент нужен для **multi-PC** (`/host`): несколько ПК админов, один активный поллер. На одном ПК достаточно `python -m bot.main`.

## Multi-PC (опционально)

- На каждом ПК админа: агент (`install-agent.ps1`) + общая папка `HOST_SYNC_DIR` (Syncthing, NAS, облако).
- Передача primary: `/host` в Telegram или Discord.

## Поддержка

- GitHub Issues — баги и предложения
- Discord-сервер — см. README (инвайт добавьте при создании сервера)

Полная документация: [README.md](README.md) · English: [README.en.md](README.en.md)
