# Модерация UX (аналоги)

Дата: 2026-08-24

## Что сделано

Перенесены идеи из публичных предложек / Suggestions / TediCross без SaaS:

- `ADMIN_CHAT_ID` — карточки в группу Telegram
- `s{N}` на карточках TG/Discord
- `DISCORD_MOD_LOG_CHANNEL_ID` — лог решений
- `DISCORD_PUBLISH_THREADS` — треды под публикацией
- `KEYWORD_BLOCKLIST` — фильтр текста до очереди

## Как устроено

| Фича | Где |
|------|-----|
| sID | `bot/core/rules.display_sid` |
| keywords | `rules.text_blocked_by_keywords` → `SubmissionService.submit` |
| admin chat | `TelegramCards.admin_chat_ids` + `bot/settings.admin_chat_id` |
| mod log | `DiscordEventSync.post_decision_log` |
| threads | `DiscordChannelPublisher._maybe_create_thread` |

## Как проверить

```powershell
# unit
.\.venv\Scripts\python.exe -m pytest tests/test_core_rules.py -q

# env (пример)
# ADMIN_CHAT_ID=-100…
# KEYWORD_BLOCKLIST=спам
# DISCORD_MOD_LOG_CHANNEL_ID=…
# DISCORD_PUBLISH_THREADS=1
```

Отправьте заявку с словом из blocklist — отказ. Approve/reject при заданном log channel — строка в канале. Публикация в Discord при `DISCORD_PUBLISH_THREADS=1` — тред.

## Ограничения

- TG→DS delete по удалению поста в канале недоступен через Bot API.
- Keyword filter — подстроки по тексту заявки **и подписям медиа**; не OCR.
- `ADMIN_CHAT_ID` заменяет DM-рассылку карточек, не дублирует оба режима.
- Имена тредов публикации: `Заявка s{N}`.
