# Sample module

Example third-party module for Suggest Bridge. Not loaded in production unless
you add it to `SB_MODULES`.

## Enable

From the repository root (adjust the path if you copied this folder elsewhere):

```env
SB_MODULES=examples/sample_module/module.py:SampleModule
```

Or as an import path after installing your package:

```env
SB_MODULES=my_package.bridge_module:MyModule
```

Restart the bot process after changing `.env`.

## What it adds

- Telegram: `/sample_ping`
- Discord: `/sample_ping`

See wiki [[Добавить-модуль]] for the full author guide.
