# Module FAQ (English)

Answers for authors of **local** extensions via `SB_MODULES`. Russian: [[FAQ-модулей]].

## Where do I start?

1. Copy the template outside the repo or run `scripts/modules/scaffold-local-module.ps1` / `.sh`.
2. Set `SB_MODULES` in `.env` → `python -m bot.core.module_loader` → restart → `curl http://127.0.0.1:8080/healthz`.
3. Full guide: [[Add-module-en]] · [[Модули]].

## Where should the file live?

**Not** in the suggest-bridge GitHub repo. Use a folder on your machine, e.g.:

- Linux: `~/suggest-bridge-modules/my_feature.py`
- Windows: `C:\suggest-bridge-modules\my_feature.py`

Use an **absolute** path in `.env`, or a repo-root-relative path only to test `examples/sample_module`.

## Multiple modules?

Comma- or newline-separated in `.env`:

```env
SB_MODULES=/opt/modules/a.py:ModuleA,/opt/modules/b.py:ModuleB
```

Duplicate entries are ignored.

## `module_loader` fails: File not found

The error prints `repo root` and `cwd`. Check:

| Cause | Fix |
|-------|-----|
| Relative path | Resolved from **clone root** first, then **process CWD** |
| Windows backslashes | Use `C:\path\file.py:Class` or forward slashes |
| Wrong class name | Format must be `file.py:ClassName` with non-empty `name` on the class |
| Code lives inside the clone | Move it outside for easier `git pull` on the core |

## Bot starts but my module does nothing

1. `curl -s http://127.0.0.1:8080/healthz` → `"checks":{"modules":false}` — check startup logs for `Failed to load SB_MODULES` or `Module … hook … failed`.
2. **`module_loader` OK, healthz false:** validate checks import/class only; hooks run when the **bot starts** — errors in `setup` / `setup_discord` / `setup_telegram`.
3. **`checks.modules: true` but no commands:** empty or commented `SB_MODULES` — healthz is ok for «0 modules».
4. Set `SB_MODULES_STRICT=1` while debugging **spec** load — aborts boot on bad allowlist entry (not hook failures).

## Discord: slash missing or duplicated

- Register slash commands in `setup_discord` — the core runs sync.
- `setup_discord` runs **once** per process; do not re-add commands on reconnect.
- After `.env` changes, **restart the whole process**, not only Discord reconnect.

## Telegram: command silent

- **Discord-only** mode → `ctx.dp` is `None`; guard in your module.
- Attach routers in `setup_telegram`, not `setup`, when you need `ctx.dp`.

## Can I disable suggest / bridge / moderation?

**No** in the current release — bundled features are part of the composition root. Your module **adds** to the process. See [[Roadmap]] «Out of scope».

## Fork or PR?

- **Fork/PR** — only for **core** bugs, docs, or kernel patches.
- **Your module** — local file + `SB_MODULES`; do not merge into [noki4angel37/suggest-bridge](https://github.com/noki4angel37/suggest-bridge).

## See also

- [[Add-module-en]] · [[Модули]] · [[Добавить-модуль]]
- `examples/sample_module/` — full TG+DS+EventBus sample
- [[Устранение-неисправностей]] — operator runbooks (`healthz`, process)
