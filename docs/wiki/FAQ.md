# FAQ

**Можно только Telegram без Discord?**  
Да. Не задавайте `DISCORD_TOKEN` (или оставьте пустым так, чтобы режим стал `telegram_only`). Нужны `BOT_TOKEN`, `ADMIN_IDS`, `CHANNEL_ID`.

**Можно только Discord?**  
Да. `DISCORD_TOKEN` + **`OWNER_DISCORD_ID`**, без `BOT_TOKEN`. Настройка каналов — `/setup_suggest`.

**Нужен ли VPS?**  
Нет. Windows ПК, Linux VPS или Docker — на выбор. Telegram long polling требует постоянно работающий процесс (primary).

**Сколько сообществ на один инстанс?**  
Рекомендуется одно (1 TG-канал + 1 DS guild). Код поддерживает несколько guild в Discord, но это не основной сценарий.

**Где хранятся заявки?**  
SQLite `bridge.db` — тексты, статусы, медиа-refs, mirror links, настройки. Путь: `BRIDGE_DB_PATH`.

**Телефон как клиент?**  
Админ может модерировать из приложений Telegram/Discord. Агент multi-PC — только на Windows PC.

**Почему пропал черновик после рестарта?**  
Telegram FSM в MemoryStorage. Данные очереди в SQLite сохраняются; незавершённый диалог — нет.

**Зачем HOST_SYNC_SECRET на одном ПК?**  
Для одиночного `python -m bot.main` без агента — не обязателен. Нужен для `install-agent.ps1` / Syncthing / подписи host-команд. Скрипт `prepare-env` генерирует его заранее.

**Почему `/admin_list` не работает?**  
Команды нет. Правильное имя: **`/admins_list`** (Discord) или `/admins` (Telegram).

**Почему `/adminhelp` нет в Discord?**  
Это **только Telegram** (личка админа). В Discord slash-справки нет — смотрите [[Команды]] и [[Слэш-команды-Discord]]. Аналоги: `/admins` → `/admins_list`, `/addadmin` → `/admin_add`, `/download` → `/download_bot`.

**Wiki и README расходятся?**  
README — обзор. Wiki — операторский справочник по `main`. Релизный тег может отставать — [CHANGELOG](https://github.com/noki4angel37/suggest-bridge/blob/main/CHANGELOG.md).

**Как сообщить о баге?**  
[GitHub Issues](https://github.com/noki4angel37/suggest-bridge/issues): ОС, режим (TG/DS/both), шаги, логи **без токенов**. Уязвимости — только Security Advisory — [[Безопасность]].
