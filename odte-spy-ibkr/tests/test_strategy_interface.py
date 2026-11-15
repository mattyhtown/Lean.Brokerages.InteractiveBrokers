"""One strategy interface for live and backtest, and no broker dependency below the runner."""

from __future__ import annotations

import ast
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import odte_spy_ibkr
from odte_spy_ibkr import runner as live_runner
from odte_spy_ibkr.backtest import simulator
from odte_spy_ibkr.chain import OptionCandidate, build_vertical_spread
from odte_spy_ibkr.config import CreditSide
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.strategy import interface
from odte_spy_ibkr.strategy.interface import OpenPosition, manage_open, propose_entry

from conftest import FIXTURE_DAY

ET = ZoneInfo("America/New_York")
PACKAGE = Path(odte_spy_ibkr.__file__).parent
BROKER_FREE = ["strategy", "chain", "risk", "providers", "backtest", "improvement", "macro", "unusual_whales"]


def at(hour: int, minute: int) -> datetime:
    return datetime.combine(FIXTURE_DAY, time(hour, minute), tzinfo=ET)


def chain() -> list[OptionCandidate]:
    return [
        OptionCandidate("SPY240603P00445000", 445.0, "P", FIXTURE_DAY, bid=0.39, ask=0.41),
        OptionCandidate("SPY240603P00444000", 444.0, "P", FIXTURE_DAY, bid=0.14, ask=0.16),
    ]


def position(credit: float = 0.25) -> OpenPosition:
    spread = build_vertical_spread(chain()[0], chain(), width=1.0)
    return OpenPosition(spread=spread, side=CreditSide.PUT, contracts=1, entry_credit=credit, opened_at=at(10, 0))


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("package", BROKER_FREE)
def test_package_does_not_import_ib_insync(package):
    files = sorted((PACKAGE / package).rglob("*.py"))
    assert files
    for path in files:
        offending = {m for m in imported_modules(path) if m.split(".")[0] == "ib_insync"}
        assert not offending, f"{path.relative_to(PACKAGE)} imports {offending}"


def test_importing_strategy_data_and_backtest_code_does_not_load_ib_insync():
    """Checked in a fresh interpreter, so transitive imports count too."""
    modules = [
        "odte_spy_ibkr.strategy.interface",
        "odte_spy_ibkr.chain",
        "odte_spy_ibkr.chain.cache_io",
        "odte_spy_ibkr.risk.sharpe_gate",
        "odte_spy_ibkr.providers.registry",
        "odte_spy_ibkr.backtest.runner",
        "odte_spy_ibkr.backtest.cache_audit",
        "odte_spy_ibkr.improvement.evaluator",
        "odte_spy_ibkr.journal",
    ]
    code = (
        "import importlib, sys\n"
        f"[importlib.import_module(m) for m in {modules!r}]\n"
        "sys.exit(1 if any(m.split('.')[0] == 'ib_insync' for m in sys.modules) else 0)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=PACKAGE.parent, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_live_and_backtest_call_the_same_strategy_functions():
    assert live_runner.propose_entry is interface.propose_entry is simulator.propose_entry
    assert live_runner.manage_open is interface.manage_open is simulator.manage_open


def test_propose_entry_selects_the_spread_from_context_and_chain(fixture_settings):
    ctx = MarketContext(spot=450.0, as_of=at(10, 0))
    intent = propose_entry(fixture_settings, ctx, chain(), now=at(10, 0))
    assert (intent.spread.short_leg.strike, intent.spread.long_leg.strike) == (445.0, 444.0)
    assert (intent.side, intent.contracts, intent.as_of) == (CreditSide.PUT, 1, at(10, 0))


def test_propose_entry_declines_without_spot_chain_or_credit(fixture_settings):
    ctx = MarketContext(spot=450.0, as_of=at(10, 0))
    assert propose_entry(fixture_settings, MarketContext(as_of=at(10, 0)), chain(), now=at(10, 0)) is None
    assert propose_entry(fixture_settings, ctx, [], now=at(10, 0)) is None
    fixture_settings.min_credit = 0.50
    assert propose_entry(fixture_settings, ctx, chain(), now=at(10, 0)) is None


def test_propose_entry_declines_in_a_macro_blackout(fixture_settings):
    ctx = MarketContext(spot=450.0, as_of=at(10, 0), macro_event_blackout=True)
    assert propose_entry(fixture_settings, ctx, chain(), now=at(10, 0)) is None


def test_manage_open_uses_the_supplied_clock_not_the_wall_clock(fixture_settings):
    ctx = MarketContext(spot=450.0)
    assert manage_open(fixture_settings, position(), ctx, now=at(12, 0)) == "hold"
    flatten_at = datetime.combine(FIXTURE_DAY, fixture_settings.flatten_time, tzinfo=ET)
    assert manage_open(fixture_settings, position(), ctx, now=flatten_at) == "flatten"


def test_stop_is_off_by_default_and_triggers_on_the_credit_multiple(fixture_settings):
    ctx = MarketContext(spot=450.0)
    assert fixture_settings.stop_loss_credit_multiple == 0.0
    assert manage_open(fixture_settings, position(0.25), ctx, now=at(12, 0), spread_mark=0.95) == "hold"
    fixture_settings.stop_loss_credit_multiple = 2.0
    assert manage_open(fixture_settings, position(0.25), ctx, now=at(12, 0), spread_mark=0.49) == "hold"
    assert manage_open(fixture_settings, position(0.25), ctx, now=at(12, 0), spread_mark=0.50) == "stop"
    assert manage_open(fixture_settings, position(0.25), ctx, now=at(12, 0)) == "hold"  # no mark, no stop


def test_put_spread_is_flattened_when_a_blackout_starts(fixture_settings):
    ctx = MarketContext(spot=450.0, macro_event_blackout=True)
    assert manage_open(fixture_settings, position(), ctx, now=at(12, 0)) == "flatten"
