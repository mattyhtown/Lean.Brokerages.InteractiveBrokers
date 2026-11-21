"""UW replay in backtests: cache layout and UW_BACKTEST_MODE=skip|neutral|block when the cache is missing."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from odte_spy_ibkr.backtest.replay import ENTRY_BLOCK_NOTE, SessionReplay, build_backtest_context
from odte_spy_ibkr.backtest.simulator import simulate_session
from odte_spy_ibkr.config import Settings, UWBacktestMode
from odte_spy_ibkr.providers.base import FlowBias
from odte_spy_ibkr.providers.uw_backtest import UwBacktestReplay

from conftest import FIXTURE_DAY, build_session, constant

ET = ZoneInfo("America/New_York")
DAY = FIXTURE_DAY
LEGS = {("P", 445.0): constant(0.40), ("P", 444.0): constant(0.15)}


def at(hour: int, minute: int) -> datetime:
    return datetime.combine(DAY, time(hour, minute), tzinfo=ET)


def with_mode(settings: Settings, mode: str) -> Settings:
    settings.uw_backtest_mode = mode
    return settings


def test_default_mode_is_neutral(fixture_settings):
    assert fixture_settings.uw_backtest_mode == UWBacktestMode.NEUTRAL
    assert Settings(UW_BACKTEST_MODE="block").uw_backtest_mode == UWBacktestMode.BLOCK
    assert Settings(UW_BACKTEST_CACHE_DIR="/x/uw").uw_backtest_cache_dir == "/x/uw"


def test_neutral_trades_on_neutral_flow_when_cache_is_missing(fixture_settings):
    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(with_mode(fixture_settings, "neutral"), session)
    assert sim.scored and len(sim.trades) == 1
    ctx = build_backtest_context(fixture_settings, day=DAY, spot=450.0)
    assert (ctx.flow_bias, ctx.provider_notes["uw_backtest"]) == (FlowBias.NEUTRAL, "uw_neutral")


def test_block_keeps_the_session_flat_and_scored(fixture_settings):
    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(with_mode(fixture_settings, "block"), session)
    assert sim.scored and sim.trades == () and sim.pnl == 0.0
    assert sim.session_reason == "no_entry:uw_cache_missing_block"
    ctx = build_backtest_context(fixture_settings, day=DAY, spot=450.0)
    assert ctx.provider_notes[ENTRY_BLOCK_NOTE] == "uw_cache_missing_block"


def test_skip_drops_the_session_from_the_scored_sample(fixture_settings):
    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(with_mode(fixture_settings, "skip"), session)
    assert not sim.scored and sim.trades == ()
    assert sim.session_reason == "uw_cache_missing_skip"
    assert build_backtest_context(fixture_settings, day=DAY, spot=450.0) is None


@pytest.mark.parametrize("mode", ["skip", "neutral", "block"])
def test_cached_flow_is_replayed_in_every_mode(fixture_settings, mode):
    cache = Path(fixture_settings.uw_backtest_cache_dir)
    cache.mkdir(parents=True)
    (cache / f"{DAY.isoformat()}.json").write_text(
        json.dumps({"collected_at": at(9, 45).isoformat(), "aggregate_bias": 0.5}), encoding="utf-8"
    )
    replay = SessionReplay(with_mode(fixture_settings, mode), DAY)
    assert replay.skip_reason is None
    ctx = replay.context(at(10, 0), spot=450.0)
    assert (ctx.flow_bias, ctx.flow_strength) == (FlowBias.BULLISH, 0.5)
    assert ENTRY_BLOCK_NOTE not in ctx.provider_notes


def test_block_mode_waits_for_the_first_snapshot(fixture_settings):
    cache = Path(fixture_settings.uw_backtest_cache_dir)
    cache.mkdir(parents=True)
    (cache / f"{DAY.isoformat()}.json").write_text(
        json.dumps({"collected_at": at(11, 0).isoformat(), "aggregate_bias": 0.5}), encoding="utf-8"
    )
    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(with_mode(fixture_settings, "block"), session)
    assert sim.trades[0].opened_at == at(11, 0)


def test_cache_without_a_capture_time_is_not_replayed(fixture_settings, caplog):
    cache = Path(fixture_settings.uw_backtest_cache_dir)
    cache.mkdir(parents=True)
    (cache / f"{DAY.isoformat()}.json").write_text(json.dumps({"aggregate_bias": -0.9}), encoding="utf-8")
    replay = UwBacktestReplay(fixture_settings)
    with caplog.at_level("WARNING"):
        assert replay.load_snapshots(DAY) == []
    assert "no collected_at" in caplog.text
    assert replay.flow_signal(DAY, at(15, 0))[0] == FlowBias.NEUTRAL


def test_uw_can_be_left_out_of_the_backtest_providers(fixture_settings):
    fixture_settings.providers_backtest_enabled = "macro_news"
    replay = SessionReplay(with_mode(fixture_settings, "skip"), DAY)
    assert replay.skip_reason is None
    assert replay.context(at(10, 0), spot=450.0).provider_notes["uw_backtest"] == "uw_disabled"
