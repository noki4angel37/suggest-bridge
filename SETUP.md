# Suggest Bridge — установка с нуля

Self-hosted бот предложки для **одного** Telegram-канала и/или **одного** Discord-сервера.

## Быстрый старт (Docker, рекомендуется)

```bash
git clone https://github.com/noki4angel37/suggest-bridge.git
cd suggest-bridge
bash scripts/deploy/prepare-env.sh   # Windows: .\scripts\deploy\prepare-env.ps1
# отредактируйте .env (токены)
docker compose up -d
docker compose logs -f
curl -s http://127.0.0.1:8080/healthz
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
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`, права **268561488** (Manage Messages + Manage Roles для setup; не Administrator).
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
git clone https://github.com/noki4angel37/suggest-bridge.git
cd suggest-bridge
sudo mkdir -p /opt/suggest-bridge /etc/suggest-bridge
sudo cp -a . /opt/suggest-bridge/
sudo python3 -m venv /opt/suggest-bridge/.venv
sudo /opt/suggest-bridge/.venv/bin/pip install -r /opt/suggest-bridge/requirements.txt
sudo cp .env.example /etc/suggest-bridge/env
# отредактируйте /etc/suggest-bridge/env (systemd читает его, не project .env)
sudo cp contrib/systemd/suggest-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now suggest-bridge
```

## Windows (zip / агент)

1. Скачайте zip из [Releases](https://github.com/noki4angel37/suggest-bridge/releases) или `/download` в боте.
2. Распакуйте, откройте PowerShell в папке:

```powershell
Copy-Item .env.example .env
# заполните .env
.\install-agent.ps1
.\run-agent.ps1
```

Агент нужен для **multi-PC** (`/host`): несколько ПК админов, один активный поллер. На одном ПК достаточно `python -m bot.main`.

## Multi-PC (опционально)

- `HOST_SYNC_SECRET` — один и тот же на всех ПК (обязателен для агента / Syncthing)
- `HOST_SYNC_DIR`, `HOST_ID`, `HOST_ROLE`, `HOST_LEASE_TTL_SEC`, `AGENT_TELEGRAM_ID`
- На каждом ПК админа: агент (`install-agent.ps1`) + общая папка `HOST_SYNC_DIR` (Syncthing)
- Передача primary: `/host` в Telegram или Discord (только админы бота)

## Health check

- `HEALTH_PORT=8080` — JSON `GET /healthz` (Telegram/Discord/lease checks)
- Docker: `HEALTHCHECK` в образе; в compose задайте `HEALTH_PORT`

## FSM / черновики

- Telegram FSM (`MemoryStorage`): черновики и шаг «ответ автору» **не переживают рестарт** процесса
- Шаг ответа/отклонения истекает через 15 минут без активности

## Поддержка

- **[Discord](https://discord.gg/F3fBdeTx94)** — вопросы, новости, помощь с установкой
- [GitHub Issues](https://github.com/noki4angel37/suggest-bridge/issues) — баги и предложения (без токенов в описании)

## Документация

| Документ | Назначение |
|----------|------------|
| **[GitHub Wiki](https://github.com/noki4angel37/suggest-bridge/wiki)** | Вся документация (источник: [docs/wiki/](docs/wiki/)) |
| [Как отправить заявку](https://github.com/noki4angel37/suggest-bridge/wiki/Как-отправить-заявку) | Для подписчиков |
| [README.md](README.md) | Обзор и быстрый старт |
| [README.en.md](README.en.md) | English overview |
