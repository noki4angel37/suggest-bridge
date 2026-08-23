# Multi-PC

Несколько Windows-ПК админов, **один** активный поллер Telegram (primary). Передача через `/host` и общую папку Syncthing.

На **одном** ПК достаточно `python -m bot.main` без агента.

## Схема

```
Admin PC A (agent)  ←Syncthing→  HOST_SYNC_DIR  ←Syncthing→  Admin PC B (agent)
                                      ↑
                              Primary: python -m bot.main
                              (один держит lease + getUpdates)
```

Файлы sync: `registry/`, `commands/`, `acks/`, `state.json` (подписи HMAC где применимо). Окно replay ~300 с.

## Установка агента

1. Zip из [Releases](https://github.com/noki4angel37/suggest-bridge/releases), `/download` (TG) или `/download_bot` (Discord).
2. На каждом ПК: **один и тот же** непустой `HOST_SYNC_SECRET` в `.env` (32+ байт hex; не `REPLACE_ME`).
3. Syncthing: folder → `HOST_SYNC_DIR` (default `%LOCALAPPDATA%\suggest-host-sync`).
   - Только **полностью доверенные** устройства с write.
   - Не синхронизируйте сюда `.env` или `bridge.db`.
4. `.\install-agent.ps1` → `.\run-agent.ps1`.
5. При необходимости `AGENT_TELEGRAM_ID` / `AGENT_DISCORD_ID`.

Переменные: [[Конфигурация]].

## Управление

| Где | Кто | Что |
|-----|-----|-----|
| Telegram `/host` | админы бота | панель, запрос передачи, force (owner) |
| Discord `/host` | **только админы бота** | ephemeral; модераторы — read-only sticky |
| `/host_accept`, `/host_reject`, `/host_cancel` | участники handover | Discord slash |
| `/host_consent` | админ на этом ПК | согласие на claim (если `HOST_REQUIRE_CONSENT`) |
| `/host_release` | админ | снять согласие и lease |
| `/host_status` | админ | lease / heartbeat |

На общих ПК: `HOST_REQUIRE_CONSENT=1`. `HOST_FORCE_CLAIM` — только handover/авария, потом уберите.

## Роли процесса

| `HOST_ROLE` | Поведение |
|-------------|-----------|
| `primary` | Сразу пытается держать бота |
| `standby` | Ждёт `go_primary` до `HOST_STANDBY_WAIT_SEC` (default 120) |

## Trust boundaries

| Зона | Риск |
|------|------|
| ПК с `.env` + токены | Полный контроль бота |
| Syncthing write peer | Высокий trust всей sync-плоскости; HMAC ≠ «можно шарить с кем угодно» |
| Модератор Discord | Модерация заявок, **не** `/host` |

Несовпадение секрета → `host-sync: bad signature`. Рассинхрон часов → `stale payload` / replay. Runbook: [[Устранение-неисправностей]].

См. [[Безопасность]].
