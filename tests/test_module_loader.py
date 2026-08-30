"""Tests for SB_MODULES plugin loader."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bot.core.module_loader import (
    REPO_ROOT,
    ModuleRegistry,
    enforce_strict_load,
    load_module_class,
    modules_strict_enabled,
    parse_module_specs,
    resolve_module_file,
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


teardown_order: list[str] = []


class TeardownFirst(BaseBridgeModule):
    name = "first"

    async def teardown(self, ctx: ModuleContext) -> None:
        teardown_order.append("first")


class TeardownSecond(BaseBridgeModule):
    name = "second"

    async def teardown(self, ctx: ModuleContext) -> None:
        teardown_order.append("second")


def test_parse_module_specs_dedup() -> None:
    assert parse_module_specs("a:b,a:b,c:d") == ["a:b", "c:d"]


def test_load_module_class_from_import_path() -> None:
    cls = load_module_class(f"{__name__}:GoodModule")
    assert cls is GoodModule


def test_resolve_module_file_from_repo_root() -> None:
    rel = "examples/sample_module/module.py"
    path = resolve_module_file(rel)
    assert path == (REPO_ROOT / rel).resolve()
    assert path.is_file()


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
        await registry.setup_discord_all(ctx)  # idempotent
        await registry.teardown_all(ctx)

    asyncio.run(run())
    assert module.hooks == [
        "setup",
        "setup_telegram",
        "setup_discord",
        "teardown",
    ]
    assert "teardown" in module.hooks


def test_registry_isolates_failing_module() -> None:
    registry = ModuleRegistry()
    summary = registry.load_specs([f"{__name__}:BadModule", f"{__name__}:GoodModule"])
    assert summary.loaded == 2

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


def test_teardown_lifo() -> None:
    teardown_order.clear()
    registry = ModuleRegistry()
    registry.load_specs([f"{__name__}:TeardownFirst", f"{__name__}:TeardownSecond"])
    ctx = ModuleContext(
        config=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
        services=object(),
        logger=__import__("logging").getLogger("test"),
    )

    async def run() -> None:
        await registry.teardown_all(ctx)

    asyncio.run(run())
    assert teardown_order == ["second", "first"]


def test_teardown_merges_discord_context() -> None:
    registry = ModuleRegistry()
    registry.load_specs([f"{__name__}:GoodModule"])
    base = ModuleContext(
        config=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
        services=object(),
        logger=__import__("logging").getLogger("test"),
    )
    discord_bot = object()
    discord_ctx = object()

    async def run() -> None:
        await registry.setup_all(base)
        await registry.setup_discord_all(
            ModuleContext(
                config=base.config,
                db=base.db,
                bus=base.bus,
                services=base.services,
                logger=base.logger,
                discord_bot=discord_bot,
                discord_ctx=discord_ctx,
            )
        )
        registry.merge_context(discord_bot=discord_bot)
        await registry.teardown_all()

    asyncio.run(run())
    assert registry.modules[0].hooks[-1] == "teardown"


def test_health_ok_false_on_load_failure() -> None:
    registry = ModuleRegistry()
    registry.load_specs(["totally.invalid.spec:NoClass"])
    assert registry.health_ok() is False


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


def test_strict_load_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SB_MODULES_STRICT", "1")
    from bot.core.module_loader import ModuleLoadSummary

    with pytest.raises(SystemExit):
        enforce_strict_load(
            ModuleLoadSummary(requested=1, loaded=0, failed_specs=("bad:x",), loaded_names=())
        )


def test_load_sample_module_from_repo_relative() -> None:
    registry = ModuleRegistry()
    summary = registry.load_specs(["examples/sample_module/module.py:SampleModule"])
    assert summary.loaded == 1
    assert registry.modules[0].name == "sample"


def test_module_loader_cli_empty() -> None:
    env = os.environ.copy()
    env.pop("SB_MODULES", None)
    result = subprocess.run(
        [sys.executable, "-m", "bot.core.module_loader"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SB_MODULES: (empty)" in result.stdout


def test_module_loader_cli_loads_sample() -> None:
    env = os.environ.copy()
    env["SB_MODULES"] = "examples/sample_module/module.py:SampleModule"
    result = subprocess.run(
        [sys.executable, "-m", "bot.core.module_loader"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "loaded 1/1" in result.stdout
    assert "sample" in result.stdout
