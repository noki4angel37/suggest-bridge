# Sample module

Example third-party module **shipped with the Suggest Bridge core**. It is not
loaded in production unless you add it to `SB_MODULES`.

Write or copy your own modules **on your machine**. Do not open a PR that
adds a community-specific module to this repository.

## Enable

From the repository root (adjust the path if you copied this folder elsewhere):

```env
SB_MODULES=examples/sample_module/module.py:SampleModule
```

Your own module — local file, not this repo:

```env
SB_MODULES=/opt/my-bot/modules/hello.py:HelloModule
```

Or as an import path after installing **your** package (your GitHub/PyPI, not
upstream suggest-bridge):

```env
SB_MODULES=my_package.bridge_module:MyModule
```

Restart the bot process after changing `.env`.

## What it adds

- Telegram: `/sample_ping`
- Discord: `/sample_ping`

See wiki [[Добавить-модуль]] for the full author guide.
