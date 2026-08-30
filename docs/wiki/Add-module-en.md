# Add a module (English)

Guide for **local** extensions to Suggest Bridge. You do not need to fork the core or open a PR with your feature — write a class on **your machine** and list it in `SB_MODULES`.

Russian: [[Добавить-модуль]] · Overview: [[Модули]] · Troubleshooting: [[FAQ-модулей]]

## Quick path (5 minutes)

1. **Scaffold** (optional) — copy the template outside this repo:

   ```powershell
   .\scripts\modules\scaffold-local-module.ps1 -OutDir C:\path\to\modules
   ```

   ```bash
   bash scripts/modules/scaffold-local-module.sh ~/suggest-bridge-modules
   ```

   Or copy manually from `examples/local_module_template/hello_module.py`.

2. **`.env`** — point at your file (not at upstream GitHub):

   ```env
   SB_MODULES=/home/you/suggest-bridge-modules/hello_module.py:HelloModule
   ```

   Multiple modules (comma-separated):

   ```env
   SB_MODULES=/opt/modules/a.py:ModuleA,/opt/modules/b.py:ModuleB
   ```

3. **Validate** without starting the full bot:

   ```bash
   python -m bot.core.module_loader
   ```

   Exit code `0` and `loaded N/N` means the spec resolves. Fix paths before restart.

4. **Restart** the bot process (`python -m bot.main`, Docker `compose restart`, or your systemd unit).

5. **Health check**:

   ```bash
   curl -s http://127.0.0.1:8080/healthz
   ```

   JSON field `checks.modules` must be `true`. For strict startup, set `SB_MODULES_STRICT=1` so a broken spec aborts boot.

## Recommended layout

| Location | Use |
|----------|-----|
| `~/suggest-bridge-modules/` or `C:\suggest-bridge-modules\` | Your `.py` files (outside the clone) |
| `examples/sample_module/` | In-repo loader demo only — not where you develop production modules |
| Your own GitHub / PyPI | Optional sharing with friends; **not** [noki4angel37/suggest-bridge](https://github.com/noki4angel37/suggest-bridge) |

## Module class

Inherit `BaseBridgeModule` from `bot.core.modules`:

| Hook | When |
|------|------|
| `setup` | Once per process — subscribe to `ctx.bus`, open connections |
| `setup_telegram` | Register aiogram `Router` on `ctx.dp` |
| `setup_discord` | `bot.tree.add_command` or Cogs — runs **once** per process |
| `teardown` | Shutdown (**LIFO**) — unsubscribe from `ctx.bus` |

`ModuleContext`: `config`, `db`, `bus`, `services`, `logger`, `telegram_bot`, `dp`, `discord_bot`, `discord_ctx`.

Bundled features (suggest, bridge, moderation, …) **cannot** be disabled via env yet — your module **adds** to the running process.

## Package import (optional)

```bash
pip install your-package   # your GitHub/PyPI, not upstream
```

```env
SB_MODULES=your_package.bridge:YourModule
```

## Do not PR your module here

Community-specific modules belong on **your** disk or **your** repo. PRs to suggest-bridge are for core bugs, docs, and kernel fixes — see [CONTRIBUTING.md](https://github.com/noki4angel37/suggest-bridge/blob/main/CONTRIBUTING.md).

## See also

- [[FAQ-модулей]] — path errors, healthz, Discord slash duplicates
- `examples/sample_module/` — full TG+DS+EventBus example
- [[Архитектура]] — process and loader wiring
