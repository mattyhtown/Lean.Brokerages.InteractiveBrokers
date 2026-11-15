"""Per-run chain coverage: sessions replayed from their own chain, sessions with none.

A session without a chain is skipped and counted. Generated prices fill it only on explicit
request, are reported apart, and never reach the scored series or the Sharpe requirement.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from odte_spy_ibkr.backtest import runner as backtest_runner
from odte_spy_ibkr.backtest.cache_audit import audit_cache
from odte_spy_ibkr.backtest.calendar import session_open
from odte_spy_ibkr.backtest.session_data import (
    NO_CHAIN,
    SOURCE_CHAIN_SNAPSHOT,
    SOURCE_OPTION_BARS,
    SOURCE_SYNTHETIC,
    spot_cache_path,
)
from odte_spy_ibkr.backtest.store import write_rows

from conftest import FakeAlpaca, chain_path, snapshot_rows, spot_rows

WEEK = {"start": date(2024, 6, 3), "end": date(2024, 6, 7)}


def _seed_two_real_days(settings) -> None:
    for day in (date(2024, 6, 3), date(2024, 6, 4)):
        write_rows(spot_cache_path(settings, "SPY", day), spot_rows(day))
        write_rows(chain_path(settings, day), snapshot_rows(day, snapshot_at=session_open(day) + timedelta(hours=1)))
    # A third day carries the old loader's signature and must not count.
    write_rows(spot_cache_path(settings, "SPY", date(2024, 6, 5)), spot_rows(date(2024, 6, 5)))
    write_rows(chain_path(settings, date(2024, 6, 5)), snapshot_rows(date(2026, 11, 16), snapshot_at=None))


def test_report_states_chain_coverage(fixture_settings, tmp_path, caplog):
    _seed_two_real_days(fixture_settings)
    report = tmp_path / "backtest.json"
    with caplog.at_level("INFO"):
        backtest_runner.run_backtest(fixture_settings, report_path=str(report), **WEEK)

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["chain_coverage"] == {
        "scope": "full_range",
        "sessions": 5,
        "own_chain": 2,
        "no_chain": 3,
        "own_chain_by_source": {SOURCE_CHAIN_SNAPSHOT: 2},
        "no_chain_dates": ["2024-06-05", "2024-06-06", "2024-06-07"],
        "own_chain_but_no_spot_bars_dates": [],
    }
    assert payload["score_days"] == 2 and payload["sessions_excluded"] == {NO_CHAIN: 3}
    assert payload["simulated"] == {
        "from": "2024-06-03",
        "to": "2024-06-07",
        "sessions": 5,
        "own_chain": 2,
        "no_chain": 3,
        "sessions_scored": 2,
        "total_pnl": payload["total_pnl_all_days"],
    }
    assert (payload["scored_window"]["from"], payload["scored_window"]["to"]) == ("2024-06-03", "2024-06-07")
    assert "synthetic" not in payload
    assert any(
        "(full_range, 2024-06-03 to 2024-06-07): 2 of 5 sessions have their own chain, 3 have none; "
        "5 session(s) simulated in total" in r.getMessage()
        for r in caplog.records
    )


def test_session_without_a_chain_is_skipped_not_filled(fixture_settings, monkeypatch):
    """Default run: the generator is never reached, and only real sessions are simulated."""
    _seed_two_real_days(fixture_settings)
    simulated: list[tuple[date, str]] = []
    real_simulate = backtest_runner.simulate_session

    def spy(settings, session):
        simulated.append((session.day, session.source))
        return real_simulate(settings, session)

    monkeypatch.setattr(backtest_runner, "simulate_session", spy)
    result = backtest_runner.execute_backtest(fixture_settings, **WEEK)
    assert simulated == [(date(2024, 6, 3), SOURCE_CHAIN_SNAPSHOT), (date(2024, 6, 4), SOURCE_CHAIN_SNAPSHOT)]

    simulated.clear()
    opted_in = backtest_runner.execute_backtest(fixture_settings, allow_synthetic=True, **WEEK)
    assert [source for _day, source in simulated].count(SOURCE_SYNTHETIC) == 3
    assert opted_in.payload["synthetic"]["sessions"] == 3
    # The scored result is untouched by the opt-in.
    for key in ("simulated", "scored_window", "score_days", "trade_count", "traded_days", "total_pnl", "trades", "chain_coverage"):
        assert opted_in.payload[key] == result.payload[key]
    assert opted_in.gate(1.5) == result.gate(1.5)


def test_run_with_keys_fetches_each_past_session_once_and_reports_full_coverage(keyed_settings, tmp_path):
    alpaca = FakeAlpaca()
    report = tmp_path / "backtest.json"
    backtest_runner.run_backtest(keyed_settings, report_path=str(report), client=alpaca.client, **WEEK)

    coverage = json.loads(report.read_text(encoding="utf-8"))["chain_coverage"]
    assert (coverage["own_chain"], coverage["no_chain"]) == (5, 0)
    assert coverage["own_chain_by_source"] == {SOURCE_OPTION_BARS: 5}
    assert not any("snapshot" in path for path in alpaca.paths())
    cached_days = sorted(p.stem for p in (Path(keyed_settings.backtest_cache_dir) / "SPY").iterdir())
    assert cached_days == ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06", "2024-06-07"]
    assert audit_cache(keyed_settings.backtest_cache_dir) == []
