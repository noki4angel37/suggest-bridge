"""Tests for SB_MODULES plugin loader."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from bot.core.module_loader import (
    ModuleRegistry,
    load_module_class,
    parse_module_specs,
)
from bot.core.modules import BaseBridgeModule, ModuleContext


class GoodModule(BaseBridgeModule):
    name = "good"

    def __init__(self) -> None:
        self.hooks: list[str] = []

    async def setup(self, ctx: ModuleContext) -> None:
        self.hooks.append("setup")

    async def setup_telegram(self, ctx: ModuleContext) -> None:
        self.hooks.append("setup_telegram")

    async def setup_discord(self, ctx: ModuleContext) -> None:
        self.hooks.append("setup_discord")

    async def teardown(self, ctx: ModuleContext) -> None:
        self.hooks.append("teardown")


class BadModule(BaseBridgeModule):
    name = "bad"

    async def setup(self, ctx: ModuleContext) -> None:
        raise RuntimeError("boom")


def test_parse_module_specs() -> None:
    assert parse_module_specs("") == []
    assert parse_module_specs("a:b,c:d") == ["a:b", "c:d"]
    assert parse_module_specs("a:b\nc:d") == ["a:b", "c:d"]


def test_load_module_class_from_import_path() -> None:
    cls = load_module_class(f"{__name__}:GoodModule")
    assert cls is GoodModule


def test_load_module_class_from_file(tmp_path: Path) -> None:
    path = tmp_path / "ext_mod.py"
    path.write_text(
        textwrap.dedent(
            """
            from bot.core.modules import BaseBridgeModule, ModuleContext

            class FileModule(BaseBridgeModule):
                name = "file"

                async def setup(self, ctx: ModuleContext) -> None:
                    pass
            """
        ),
        encoding="utf-8",
    )
    cls = load_module_class(f"{path}:FileModule")
    instance = cls()
    assert instance.name == "file"


def test_registry_runs_hooks() -> None:
    registry = ModuleRegistry()
    registry.load_specs([f"{__name__}:GoodModule"])
    assert len(registry.modules) == 1
    module = registry.modules[0]
    assert isinstance(module, GoodModule)

    ctx = ModuleContext(
        config=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
        services=object(),
        logger=__import__("logging").getLogger("test"),
    )

    async def run() -> None:
        await registry.setup_all(ctx)
        await registry.setup_telegram_all(ctx)
        await registry.setup_discord_all(ctx)
        await registry.teardown_all(ctx)

    asyncio.run(run())
    assert module.hooks == [
        "setup",
        "setup_telegram",
        "setup_discord",
        "teardown",
    ]


def test_registry_isolates_failing_module() -> None:
    registry = ModuleRegistry()
    registry.load_specs([f"{__name__}:BadModule", f"{__name__}:GoodModule"])
    assert len(registry.modules) == 2

    ctx = ModuleContext(
        config=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
        services=object(),
        logger=__import__("logging").getLogger("test"),
    )

    async def run() -> None:
        await registry.setup_all(ctx)

    asyncio.run(run())
    good = registry.modules[1]
    assert isinstance(good, GoodModule)
    assert good.hooks == ["setup"]


def test_build_bridge_loads_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.bridge import build_bridge
    from bot.config import BridgeConfig, RunMode

    monkeypatch.setenv("SB_MODULES", f"{__name__}:GoodModule")
    config = BridgeConfig(
        run_mode=RunMode.telegram_only,
        bot_token="123456:TEST",
        admin_ids=frozenset({1}),
        channel_id=-100123,
        bridge_db_path=str(tmp_path / "bridge.db"),
    )
    bridge = build_bridge(config)
    assert bridge.modules.loaded_specs == (f"{__name__}:GoodModule",)
