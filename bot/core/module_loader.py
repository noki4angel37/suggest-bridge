"""Load third-party modules from SB_MODULES allowlist."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

from bot.core.modules import BaseBridgeModule, BridgeModule, ModuleContext

logger = logging.getLogger(__name__)

ENV_MODULES = "SB_MODULES"


def parse_module_specs(raw: str | None) -> list[str]:
    """Split comma/newline-separated import specs."""
    if not raw:
        return []
    specs: list[str] = []
    for part in raw.replace("\n", ",").split(","):
        spec = part.strip()
        if spec:
            specs.append(spec)
    return specs


def load_module_class(spec: str) -> type[BaseBridgeModule]:
    """Resolve `pkg.mod:ClassName` or `/path/to/file.py:ClassName`."""
    if ":" not in spec:
        raise ValueError(f"SB_MODULES entry must be module:ClassName, got {spec!r}")
    path_or_module, class_name = spec.rsplit(":", 1)
    path_or_module = path_or_module.strip()
    class_name = class_name.strip()
    if not path_or_module or not class_name:
        raise ValueError(f"Invalid SB_MODULES spec: {spec!r}")

    if _looks_like_file_path(path_or_module):
        path = Path(path_or_module).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Module file not found: {path}")
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


def instantiate_module(cls: type[Any]) -> BridgeModule:
    instance = cls()
    if not hasattr(instance, "name"):
        raise TypeError(f"{cls.__name__} must define name")
    return instance  # type: ignore[return-value]


class ModuleRegistry:
    """Loaded modules and lifecycle hooks."""

    def __init__(self) -> None:
        self._modules: list[BridgeModule] = []
        self._loaded_specs: list[str] = []

    @property
    def modules(self) -> tuple[BridgeModule, ...]:
        return tuple(self._modules)

    @property
    def loaded_specs(self) -> tuple[str, ...]:
        return tuple(self._loaded_specs)

    def load_specs(self, specs: list[str]) -> None:
        """Instantiate modules from specs; one failure does not block others."""
        for spec in specs:
            try:
                cls = load_module_class(spec)
                module = instantiate_module(cls)
                self._modules.append(module)
                self._loaded_specs.append(spec)
                logger.info("Loaded module %s from %s", module.name, spec)
            except Exception:
                logger.exception("Failed to load SB_MODULES entry %s", spec)

    def load_from_env(self, env_key: str = ENV_MODULES) -> None:
        raw = os.environ.get(env_key, "").strip()
        specs = parse_module_specs(raw)
        if specs:
            logger.info("SB_MODULES: loading %s entries", len(specs))
        self.load_specs(specs)

    async def setup_all(self, ctx: ModuleContext) -> None:
        await self._run_hook("setup", ctx)

    async def setup_telegram_all(self, ctx: ModuleContext) -> None:
        await self._run_hook("setup_telegram", ctx)

    async def setup_discord_all(self, ctx: ModuleContext) -> None:
        await self._run_hook("setup_discord", ctx)

    async def teardown_all(self, ctx: ModuleContext) -> None:
        await self._run_hook("teardown", ctx)

    async def _run_hook(self, hook: str, ctx: ModuleContext) -> None:
        for module in self._modules:
            fn = getattr(module, hook, None)
            if fn is None:
                continue
            try:
                await fn(ctx)
            except Exception:
                logger.exception(
                    "Module %s hook %s failed", getattr(module, "name", module), hook
                )
