# Contributing

Thanks for helping improve Suggest Bridge.

## Bug reports

1. Check [existing issues](https://github.com/noki4angel37/suggest-bridge/issues).
2. Open a new issue with: OS, run mode (TG/DS/both), steps to reproduce, logs (redact tokens).

## Pull requests

1. Fork and branch from `main`.
2. Run tests: `pytest -q`
3. Keep changes focused; update CHANGELOG for user-visible fixes.

## Modules

Built-in features (suggest, bridge, moderation, pass, …) ship with the core as the bundled kit — not as a slot you replace via env.

**Third-party modules** live on the developer’s machine (or a package you install yourself). Point `SB_MODULES` at a local `.py` or import spec, then restart. Do **not** open PRs that add community-specific modules or features to this repository. Core bugfixes, docs, and kernel patches remain welcome.

See wiki [Модули](https://github.com/noki4angel37/suggest-bridge/wiki/Модули) and [Добавить модуль](https://github.com/noki4angel37/suggest-bridge/wiki/Добавить-модуль). The in-repo `examples/sample_module/` is a loader sample, not a drop-off for your code.

## Code

- Python 3.11+
- UI strings stay Russian for now; docs may be RU + EN.

## Community

- **[Discord](https://discord.gg/F3fBdeTx94)** — questions, install help, release news
- **[Wiki](https://github.com/noki4angel37/suggest-bridge/wiki)** — all documentation (members and operators)
