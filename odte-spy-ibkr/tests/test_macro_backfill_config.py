"""Macro calendar backfill command and YAML configuration loading."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from odte_spy_ibkr.config import CreditSide, MacroBacktestMode, UWBacktestMode, load_settings
from odte_spy_ibkr.macro.__main__ import backfill_calendar, main as macro_main, normalize_event
from odte_spy_ibkr.providers.macro_news import load_macro_calendar_for_year

ET = ZoneInfo("America/New_York")


def test_backfill_writes_the_years_events_normalized_and_sorted(tmp_path):
    source = tmp_path / "events.yaml"
    source.write_text(
        "events:\n"
        "  - name: FOMC\n    release_at: '2024-06-12T14:00:00'\n    tier: 1\n"
        "  - name: CPI\n    release_at: '2024-06-12T08:30:00-04:00'\n"
        "  - name: CPI\n    release_at: '2025-01-15T08:30:00-05:00'\n"
        "  - name: undated\n",
        encoding="utf-8",
    )
    out = backfill_calendar(source_yaml=source, year=2024, cache_dir=tmp_path / "cache")
    assert out == tmp_path / "cache" / "macro_calendar" / "2024.json"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["year"] == 2024
    assert [(e["name"], e["release_at"], e["tier"]) for e in payload["events"]] == [
        ("CPI", "2024-06-12T08:30:00-04:00", 1),
        ("FOMC", "2024-06-12T14:00:00-04:00", 1),  # naive times are US/Eastern
    ]


def test_backfill_merges_without_duplicating_and_accepts_csv(tmp_path):
    first = tmp_path / "a.yaml"
    first.write_text("events:\n  - name: CPI\n    release_at: '2024-06-12T08:30:00-04:00'\n", encoding="utf-8")
    second = tmp_path / "b.csv"
    second.write_text(
        "name,release_at,tier,actual\nCPI,2024-06-12T12:30:00Z,1,3.3\nNFP,2024-06-07T08:30:00,1,\n",
        encoding="utf-8",
    )
    backfill_calendar(source_yaml=first, year=2024, cache_dir=tmp_path / "cache")
    out = backfill_calendar(source_yaml=second, year=2024, cache_dir=tmp_path / "cache")
    events = json.loads(out.read_text(encoding="utf-8"))["events"]
    assert [e["name"] for e in events] == ["NFP", "CPI"]
    assert events[1]["actual"] == "3.3"  # same release instant: the newer source replaces the entry


def test_backfilled_calendar_is_what_the_backtest_reads(tmp_path, fixture_settings, monkeypatch, capsys):
    source = tmp_path / "events.json"
    source.write_text(json.dumps([{"name": "FOMC", "release_at": "2024-06-12T14:00:00-04:00"}]), encoding="utf-8")
    monkeypatch.setattr("odte_spy_ibkr.macro.__main__.load_settings", lambda: fixture_settings)
    macro_main(["backfill", "--year", "2024", "--source", str(source)])
    assert "1 event(s)" in capsys.readouterr().out
    events = load_macro_calendar_for_year(fixture_settings, 2024)
    assert [e["name"] for e in events] == ["FOMC"]


def test_normalize_event_requires_a_release_time():
    assert normalize_event({"name": "CPI"}) is None
    event = normalize_event({"name": " CPI ", "release_at": datetime(2024, 6, 12, 8, 30), "tier": "2", "note": ""})
    assert event == {"name": "CPI", "release_at": "2024-06-12T08:30:00-04:00", "tier": 2}


def test_yaml_config_is_validated_like_environment_input(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "strategy:\n  credit_side: put\n  entry_cutoff: '14:30'\n  stop_loss_credit_multiple: 2\n"
        "unusual_whales:\n  backtest_mode: block\n  backtest_cache_dir: /data/uw\n"
        "macro:\n  backtest_mode: strict\n"
        "backtest:\n  slippage_ticks: 2\n  commission_per_contract: 0.5\n  require_sharpe_pass: true\n"
        "risk:\n  sharpe_gate_enabled: true\n  journal_path: /data/journal.jsonl\n",
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.credit_side == CreditSide.PUT
    assert settings.entry_cutoff == time(14, 30)
    assert settings.stop_loss_credit_multiple == 2.0
    assert settings.uw_backtest_mode == UWBacktestMode.BLOCK and settings.uw_backtest_cache_dir == "/data/uw"
    assert settings.macro_backtest_mode == MacroBacktestMode.STRICT
    assert (settings.backtest_slippage_ticks, settings.backtest_commission_per_contract) == (2.0, 0.5)
    assert settings.backtest_require_sharpe_pass is True
    assert settings.sharpe_gate_enabled is True and settings.journal_path == "/data/journal.jsonl"


def test_example_config_loads_with_every_safety_switch_off():
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    settings = load_settings(example)
    assert settings.orders_allowed is False
    assert settings.live_trading is False and settings.read_only is True and settings.enable_orders is False
    assert settings.sharpe_gate_enabled is False
    assert settings.backtest_require_sharpe_pass is False
