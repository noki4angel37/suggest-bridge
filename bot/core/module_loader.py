"""Load third-party modules from SB_MODULES allowlist."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bot.core.modules import BaseBridgeModule, BridgeModule, ModuleContext

logger = logging.getLogger(__name__)

ENV_MODULES = "SB_MODULES"
ENV_MODULES_STRICT = "SB_MODULES_STRICT"
HOOK_NAMES = ("setup", "setup_telegram", "setup_discord", "teardown")

# bot/core/module_loader.py -> repo root (suggest-bridge/)
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ModuleLoadSummary:
    """Result of parsing and loading SB_MODULES."""

    requested: int
    loaded: int
    failed_specs: tuple[str, ...]
    loaded_names: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.loaded == self.requested


def parse_module_specs(raw: str | None) -> list[str]:
    """Split comma/newline-separated import specs."""
    if not raw:
        return []
    specs: list[str] = []
    seen: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        spec = part.strip()
        if not spec or spec in seen:
            continue
        seen.add(spec)
        specs.append(spec)
    return specs


def resolve_module_file(path_or_module: str) -> Path:
    """Resolve file spec: absolute, repo-root-relative, then CWD-relative."""
    raw = Path(path_or_module).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    from_repo = (REPO_ROOT / raw).resolve()
    if from_repo.is_file():
        return from_repo
    from_cwd = raw.resolve()
    return from_cwd


def load_module_class(spec: str) -> type[BaseBridgeModule]:
    """Resolve `pkg.mod:ClassName` or `path/to/file.py:ClassName`."""
    if ":" not in spec:
        raise ValueError(f"SB_MODULES entry must be module:ClassName, got {spec!r}")
    path_or_module, class_name = spec.rsplit(":", 1)
    path_or_module = path_or_module.strip()
    class_name = class_name.strip()
    if not path_or_module or not class_name:
        raise ValueError(f"Invalid SB_MODULES spec: {spec!r}")

    if _looks_like_file_path(path_or_module):
        path = resolve_module_file(path_or_module)
        if not path.is_file():
            raise FileNotFoundError(
                f"Module file not found: {path} "
                f"(repo root {REPO_ROOT}, cwd {Path.cwd()})"
            )
        mod_name = f"sb_ext_{path.stem}_{abs(hash(path)) & 0xFFFF_FFFF:x}"
        file_spec = importlib.util.spec_from_file_location(mod_name, path)
        if file_spec is None or file_spec.loader is None:
            raise ImportError(f"Cannot load module from {path}")
        module = importlib.util.module_from_spec(file_spec)
        file_spec.loader.exec_module(module)
        cls = getattr(module, class_name)
    else:
        module = importlib.import_module(path_or_module)
        cls = getattr(module, class_name)

    if not isinstance(cls, type):
        raise TypeError(f"{spec!r} did not resolve to a class")
    return cls


def _looks_like_file_path(value: str) -> bool:
    if value.endswith(".py"):
        return True
    return "/" in value or "\\" in value


def _validate_module_class(cls: type[Any]) -> None:
    for hook in HOOK_NAMES:
        fn = getattr(cls, hook, None)
        if fn is None:
            continue
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"{cls.__name__}.{hook} must be async")


def instantiate_module(cls: type[Any]) -> BridgeModule:
    _validate_module_class(cls)
    instance = cls()
    name = getattr(instance, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise TypeError(f"{cls.__name__} must define non-empty name")
    return instance  # type: ignore[return-value]


def modules_strict_enabled() -> bool:
    raw = os.environ.get(ENV_MODULES_STRICT, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class ModuleRegistry:
    """Loaded modules, merged runtime context, and lifecycle hooks."""

    def __init__(self) -> None:
        self._modules: list[BridgeModule] = []
        self._loaded_specs: list[str] = []
        self._failed_specs: list[str] = []
        self._hook_failures: list[tuple[str, str]] = []
        self._ctx: ModuleContext | None = None
        self._discord_setup_done = False
        self._last_summary: ModuleLoadSummary | None = None

    @property
    def modules(self) -> tuple[BridgeModule, ...]:
        return tuple(self._modules)

    @property
    def loaded_specs(self) -> tuple[str, ...]:
        return tuple(self._loaded_specs)

    @property
    def failed_specs(self) -> tuple[str, ...]:
        return tuple(self._failed_specs)

    @property
    def hook_failures(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._hook_failures)

    @property
    def last_summary(self) -> ModuleLoadSummary | None:
        return self._last_summary

    def bind_context(self, ctx: ModuleContext) -> None:
        """Base context; platform fields merged before hooks."""
        self._ctx = ctx

    def merge_context(self, **fields: Any) -> None:
        if self._ctx is None:
            return
        for key, value in fields.items():
            if value is not None:
                object.__setattr__(self._ctx, key, value)

    def health_ok(self) -> bool:
        if self._last_summary is None:
            return True
        return self._last_summary.ok and not self._hook_failures

    def load_specs(self, specs: list[str]) -> ModuleLoadSummary:
        """Instantiate modules from specs; one failure does not block others."""
        self._failed_specs.clear()
        before = len(self._modules)
        for spec in specs:
            try:
                cls = load_module_class(spec)
                module = instantiate_module(cls)
                self._modules.append(module)
                self._loaded_specs.append(spec)
                logger.info("Loaded module %s from %s", module.name, spec)
            except Exception:
                self._failed_specs.append(spec)
                logger.exception("Failed to load SB_MODULES entry %s", spec)
        summary = ModuleLoadSummary(
            requested=len(specs),
            loaded=len(self._modules) - before,
            failed_specs=tuple(self._failed_specs),
            loaded_names=tuple(m.name for m in self._modules[before:]),
        )
        self._last_summary = summary
        self._log_summary(summary)
        return summary

    def load_from_env(self, env_key: str = ENV_MODULES) -> ModuleLoadSummary:
        raw = os.environ.get(env_key, "").strip()
        specs = parse_module_specs(raw)
        if specs:
            logger.info("SB_MODULES: loading %s entries", len(specs))
        return self.load_specs(specs)

    async def setup_all(self, ctx: ModuleContext) -> None:
        self.bind_context(ctx)
        await self._run_hook("setup", ctx)

    async def setup_telegram_all(self, ctx: ModuleContext) -> None:
        self.merge_context(
            telegram_bot=ctx.telegram_bot,
            dp=ctx.dp,
        )
        await self._run_hook("setup_telegram", ctx)

    async def setup_discord_all(self, ctx: ModuleContext) -> None:
        """Run once per process; Discord reconnect must not re-register modules."""
        self.merge_context(
            discord_bot=ctx.discord_bot,
            discord_ctx=ctx.discord_ctx,
        )
        if self._discord_setup_done:
            logger.debug("setup_discord skipped (already ran for this process)")
            return
        self._discord_setup_done = True
        await self._run_hook("setup_discord", ctx)

    async def teardown_all(self, ctx: ModuleContext | None = None) -> None:
        final = ctx or self._ctx
        if final is None:
            return
        if self._ctx is not None:
            final = replace(
                final,
                discord_bot=self._ctx.discord_bot,
                discord_ctx=self._ctx.discord_ctx,
                telegram_bot=self._ctx.telegram_bot,
                dp=self._ctx.dp,
            )
        await self._run_hook("teardown", final, reverse=True)

    def _log_summary(self, summary: ModuleLoadSummary) -> None:
        if summary.requested == 0:
            return
        if summary.failed_specs:
            logger.warning(
                "SB_MODULES: loaded %s/%s modules (%s); failed: %s",
                summary.loaded,
                summary.requested,
                ", ".join(summary.loaded_names) or "—",
                ", ".join(summary.failed_specs),
            )
        else:
            logger.info(
                "SB_MODULES: loaded %s/%s modules (%s)",
                summary.loaded,
                summary.requested,
                ", ".join(summary.loaded_names),
            )

    async def _run_hook(
        self,
        hook: str,
        ctx: ModuleContext,
        *,
        reverse: bool = False,
    ) -> None:
        modules = reversed(self._modules) if reverse else self._modules
        for module in modules:
            fn = getattr(module, hook, None)
            if fn is None:
                continue
            module_ctx = replace(ctx, logger=ctx.logger.getChild(module.name))
            try:
                await fn(module_ctx)
            except Exception:
                self._hook_failures.append((module.name, hook))
                logger.exception(
                    "Module %s hook %s failed", getattr(module, "name", module), hook
                )


def enforce_strict_load(summary: ModuleLoadSummary) -> None:
    """Exit process when SB_MODULES_STRICT and any spec failed to load."""
    if not modules_strict_enabled():
        return
    if summary.requested and not summary.ok:
        raise SystemExit(
            f"SB_MODULES_STRICT: {len(summary.failed_specs)} module(s) failed to load"
        )


if __name__ == "__main__":
    from bot.config import bootstrap_env

    bootstrap_env()
    specs = parse_module_specs(os.environ.get(ENV_MODULES, ""))
    registry = ModuleRegistry()
    summary = registry.load_specs(specs)
    if summary.requested == 0:
        print("SB_MODULES: (empty)")
        raise SystemExit(0)
    print(
        f"SB_MODULES: loaded {summary.loaded}/{summary.requested} "
        f"({', '.join(summary.loaded_names) or '—'})"
    )
    if summary.failed_specs:
        print("failed:", ", ".join(summary.failed_specs))
        print(f"repo root: {REPO_ROOT}")
        print(f"cwd: {Path.cwd()}")
        raise SystemExit(1)
    raise SystemExit(0)
