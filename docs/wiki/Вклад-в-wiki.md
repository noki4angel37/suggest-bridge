# Вклад в wiki

Живая wiki на GitHub: [github.com/noki4angel37/suggest-bridge/wiki](https://github.com/noki4angel37/suggest-bridge/wiki)

**Источник правды** — каталог `docs/wiki/` в **корне репозитория** (не веб-редактор GitHub Wiki). Подробнее о схеме «источник → публикация»: [docs/WIKI.md](https://github.com/noki4angel37/suggest-bridge/blob/main/docs/WIKI.md).

## Как править

1. Клонируйте репозиторий [suggest-bridge](https://github.com/noki4angel37/suggest-bridge).
2. Редактируйте или добавляйте файлы в `docs/wiki/` (имя файла без `.md` = имя страницы wiki, пробелы → дефисы).
3. Для русских страниц используйте внутренние ссылки в формате `[[Имя-страницы]]`.
4. Для английских страниц — `[[How-to-submit-en]]`, `[[Member-FAQ-en]]`, `[[Home-en]]` и т.д.
5. Откройте **Pull Request** в `main` — правки ревьюятся вместе с кодом.

## Публикация на GitHub Wiki

После merge в `main` (или локально для проверки) синхронизируйте wiki:

```powershell
# из корня клонированного репозитория
.\scripts\deploy\publish-wiki.ps1
```

Скрипт клонирует `suggest-bridge.wiki.git`, копирует `docs/wiki/*`, коммитит и пушит в ветку `master` wiki.

**Первый запуск:** если wiki ещё не инициализирована — один раз создайте страницу Home через UI GitHub (Settings → Wikis), затем используйте скрипт.

## Не редактируйте живую wiki долгосрочно

Правки только через веб-UI GitHub Wiki **перезапишутся** при следующем `publish-wiki.ps1`. Долгосрочный workflow:

- правки в `docs/wiki/` → PR → merge → `publish-wiki.ps1`.

Исключение: срочная опечатка на живой wiki — можно поправить в UI, но **дублируйте** изменение в `docs/wiki/` в том же или следующем PR.

## Служебные файлы

| Файл | Назначение |
|------|------------|
| `_Sidebar.md` | Боковое меню wiki |
| `_Footer.md` | Подвал wiki |

При добавлении важной страницы обновите `_Sidebar.md` и таблицу в [docs/WIKI.md](https://github.com/noki4angel37/suggest-bridge/blob/main/docs/WIKI.md).

## Стиль

- Подписчикам — простой язык, без токенов и внутренних путей хоста.
- Админам — ссылки на [[Конфигурация]], [[Безопасность]], репозиторные `SETUP.md` / `SECURITY.md`.
- Английские landing-страницы — суффикс `-en` в имени файла.

См. также: [[Разработка]] · [[Home]]
