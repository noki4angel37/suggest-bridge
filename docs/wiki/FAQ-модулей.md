# FAQ: свои модули (`SB_MODULES`)

Ответы для авторов **локальных** расширений. English: [[Add-module-en]].

## С чего начать?

1. Скопируйте шаблон: `examples/local_module_template/hello_module.py` **вне** репозитория или запустите `scripts/modules/scaffold-local-module.ps1` / `.sh`.
2. Пропишите путь в `.env` → `python -m bot.core.module_loader` → перезапуск → `curl http://127.0.0.1:8080/healthz`.
3. Подробный гайд: [[Добавить-модуль]].

## Куда класть файл модуля?

**Не** в GitHub suggest-bridge и **не** обязательно внутрь клона. Рекомендуется отдельная папка на вашей машине, например:

- Linux: `~/suggest-bridge-modules/my_feature.py`
- Windows: `C:\suggest-bridge-modules\my_feature.py`

В `.env` — **абсолютный** путь или путь от корня клона (только для проверки `examples/sample_module`).

## Как указать несколько модулей?

Через запятую или с новой строки в `.env`:

```env
SB_MODULES=/opt/modules/a.py:ModuleA,/opt/modules/b.py:ModuleB
```

Дубликаты записей игнорируются.

## `module_loader` падает: File not found

Сообщение содержит `repo root …` и `cwd …`. Проверьте:

| Причина | Решение |
|---------|---------|
| Относительный путь | Сначала ищется от **корня клона** suggest-bridge, затем от **CWD** процесса |
| Windows `\` | Используйте `C:\path\file.py:Class` или прямые слэши |
| Опечатка в `ClassName` | Должно быть `file.py:ClassName` — класс с атрибутом `name` |
| Файл внутри репо «навсегда» | Вынесите свой код наружу — так проще обновлять ядро через `git pull` |

## Бот стартует, но модуль не работает

1. `curl -s http://127.0.0.1:8080/healthz` → `"checks":{"modules":false}` — смотрите логи при старте (`Failed to load SB_MODULES`).
2. Включите `SB_MODULES_STRICT=1` на время отладки — процесс не поднимется с битой записью.
3. Хук упал после загрузки — в логах `Module … hook … failed`; остальные модули и встроенные фичи могут работать.

## Discord: команда не появилась / дубли

- Регистрируйте slash в `setup_discord` — sync делает ядро.
- `setup_discord` вызывается **один раз** за процесс; при reconnect Discord хук не повторяется — не добавляйте команды повторно вручную.
- После смены `.env` нужен **полный перезапуск** процесса, не только reconnect Discord.

## Telegram: команда не отвечает

- Режим **только Discord** — `ctx.dp` будет `None`; модуль должен это проверять.
- Router подключайте в `setup_telegram`, не в `setup`, если нужен `ctx.dp`.

## Можно ли отключить предложку / мост?

**Нет** в текущей версии — встроенные модули часть composition root. Ваш модуль **дополняет** процесс. См. [[Roadmap]] «Вне scope».

## Нужен ли fork или PR?

- **Fork/PR** — только если правите **ядро** или документацию upstream.
- **Свой модуль** — файл у себя + `SB_MODULES`; в этот репозиторий не merge.

## См. также

- [[Модули]] — API и правила allowlist
- [[Добавить-модуль]] — пошаговый гайд
- [[Add-module-en]] — English author guide
- `examples/sample_module/` — полный пример с EventBus
