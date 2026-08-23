# GitHub Wiki — источник и публикация

Живая wiki: [github.com/noki4angel37/suggest-bridge/wiki](https://github.com/noki4angel37/suggest-bridge/wiki)

**Источник правды:** каталог [`docs/wiki/`](wiki/) в этом репозитории (ревью в PR). GitHub Wiki (`*.wiki.git`) — только публикация; не редактируйте страницы долговременно через веб-UI GitHub (правки перезапишутся при следующей публикации).

## Страницы

| Файл | Wiki-страница |
|------|----------------|
| `Home.md` | Home |
| `Быстрый-старт.md` | Быстрый-старт |
| `Архитектура.md` | Архитектура |
| `Telegram.md` | Telegram |
| `Discord.md` | Discord |
| `Команды.md` | Команды |
| `Модерация.md` | Модерация |
| `Конфигурация.md` | Конфигурация |
| `Multi-PC.md` | Multi-PC |
| `Деплой.md` | Деплой |
| `Эксплуатация.md` | Эксплуатация |
| `Безопасность.md` | Безопасность |
| `Устранение-неисправностей.md` | Устранение-неисправностей |
| `FAQ.md` | FAQ |
| `Разработка.md` | Разработка |
| `Глоссарий.md` | Глоссарий |
| `_Sidebar.md` | сайдбар |
| `_Footer.md` | футер |

## Публикация (ручная)

1. В репозитории: **Settings → Features → Wikis** (включено).
2. Если wiki ещё пустая — создайте первую страницу Home в UI (инициализирует `suggest-bridge.wiki.git`).
3. Синхронизация из источника правды:

```powershell
cd c:\!projects\suggest-bridge
.\scripts\deploy\publish-wiki.ps1
```

Эквивалент вручную: клон `suggest-bridge.wiki.git` → `Copy-Item docs\wiki\*` → commit → `git push origin master`.

Ветка wiki обычно `master` (не `main`).

4. Проверьте в браузере: сайдбар, [[ссылки]], mermaid на «Архитектура».

Автосинк GitHub Action в этом репозитории пока не используется.
