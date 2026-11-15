"""No lookahead on a fixture day: every input is visible only from the moment it existed.

The chain section runs against a mocked Alpaca client. It is regression cover for the first
backtest loader, which cached today's chain snapshot under the historical date being
backtested and cached an HTTP error as an empty chain.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from odte_spy_ibkr.backtest.cache_audit import audit_cache, main as cache_audit_main
from odte_spy_ibkr.backtest.calendar import session_close, session_open
from odte_spy_ibkr.backtest.data_loader import load_chain_for_day
from odte_spy_ibkr.backtest.replay import SessionReplay, build_backtest_context
from odte_spy_ibkr.backtest.session_data import (
    NO_CHAIN,
    NO_SPOT,
    NOT_COMPLETED,
    SOURCE_CHAIN_SNAPSHOT,
    SOURCE_OPTION_BARS,
    load_session,
    spot_cache_path,
)
from odte_spy_ibkr.backtest.simulator import simulate_session
from odte_spy_ibkr.backtest.store import read_rows, write_rows
from odte_spy_ibkr.chain import OptionCandidate
from odte_spy_ibkr.chain.cache_io import save_chain_json
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import FlowBias
from odte_spy_ibkr.providers.macro_news import score_headlines_before

from conftest import (
    FIXTURE_DAY,
    LATER_DAY,
    FakeAlpaca,
    after_close,
    build_session,
    cache_files,
    chain_path,
    constant,
    snapshot_rows,
    spot_rows,
)

ET = ZoneInfo("America/New_York")
DAY = FIXTURE_DAY


def at(hour: int, minute: int) -> datetime:
    return datetime.combine(DAY, time(hour, minute), tzinfo=ET)


def test_spot_bar_is_visible_only_after_it_closes(fixture_settings):
    session = build_session(
        fixture_settings,
        spot=lambda t: 450.0 if t <= at(11, 0) else 460.0,  # jump in the bar that starts at 11:00
        legs={},
    )
    assert session.spot_at(at(11, 0)) == 450.0
    assert session.spot_at(at(11, 1)) == 460.0
    assert session.spot_at(at(9, 30)) is None  # nothing has printed at the open


def test_chain_shows_only_prints_up_to_the_decision_time(fixture_settings):
    session = build_session(
        fixture_settings,
        spot=constant(450.0),
        legs={("P", 445.0): lambda t: 0.40 if t <= at(11, 0) else 0.90},
    )
    (before,) = session.chain_at(at(11, 0))
    (after,) = session.chain_at(at(11, 1))
    assert before.mid == pytest.approx(0.40)
    assert after.mid == pytest.approx(0.90)
    # Cumulative volume counts completed bars only: 90 bars by 11:00.
    assert before.volume == pytest.approx(900.0)


def test_entry_waits_until_the_credit_exists(fixture_settings):
    """The spread is too cheap all morning and reaches MIN_CREDIT in the bar starting 12:00."""
    session = build_session(
        fixture_settings,
        spot=constant(450.0),
        legs={
            ("P", 445.0): lambda t: 0.20 if t <= at(12, 0) else 0.40,
            ("P", 444.0): constant(0.15),
        },
    )
    sim = simulate_session(fixture_settings, session)
    assert sim.trades[0].opened_at == at(12, 5)  # first 5-minute decision after the 12:01 print
    assert sim.trades[0].entry_credit == pytest.approx(0.24)


def test_decisions_before_t_do_not_depend_on_data_after_t(fixture_settings):
    """Two sessions identical up to 13:00 and different afterwards open the same trade."""
    fixture_settings.stop_loss_credit_multiple = 2.0
    legs = {("P", 444.0): constant(0.15)}
    calm = build_session(
        fixture_settings, spot=constant(450.0), legs={**legs, ("P", 445.0): constant(0.40)}
    )
    crash = build_session(
        fixture_settings,
        spot=lambda t: 450.0 if t <= at(13, 0) else 440.0,
        legs={**legs, ("P", 445.0): lambda t: 0.40 if t <= at(13, 0) else 0.95},
    )
    calm_trade = simulate_session(fixture_settings, calm).trades[0]
    crash_trade = simulate_session(fixture_settings, crash).trades[0]

    assert (calm_trade.opened_at, calm_trade.entry_credit, calm_trade.short_strike) == (
        crash_trade.opened_at,
        crash_trade.entry_credit,
        crash_trade.short_strike,
    )
    assert calm_trade.exit_reason == "time_exit"
    assert crash_trade.exit_reason == "stop"
    assert crash_trade.closed_at == at(13, 1)  # the first minute the move was observable


def test_uw_flow_uses_latest_snapshot_collected_by_then(fixture_settings):
    sessions = Path(fixture_settings.uw_session_jsonl_dir)
    sessions.mkdir(parents=True)
    snapshots = [
        {"collected_at": at(10, 30).isoformat(), "bundle": {"aggregate_bias": 0.4}},
        {"collected_at": at(13, 0).isoformat(), "bundle": {"aggregate_bias": -0.5}},
    ]
    (sessions / f"{DAY.isoformat()}.jsonl").write_text(
        "".join(json.dumps(snap) + "\n" for snap in snapshots), encoding="utf-8"
    )

    replay = SessionReplay(fixture_settings, DAY)
    early = replay.context(at(10, 0), spot=450.0)
    morning = replay.context(at(11, 0), spot=450.0)
    afternoon = replay.context(at(13, 0), spot=450.0)

    assert (early.flow_bias, early.provider_notes["uw_backtest"]) == (FlowBias.NEUTRAL, "uw_not_yet_collected")
    assert (morning.flow_bias, morning.flow_strength) == (FlowBias.BULLISH, 0.4)
    assert (afternoon.flow_bias, afternoon.flow_strength) == (FlowBias.BEARISH, 0.5)


def test_headline_is_scored_only_once_published_and_within_lookback(fixture_settings):
    cache = Path(fixture_settings.backtest_cache_dir) / "macro_news" / f"{DAY.isoformat()}.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            [
                {
                    "published_at": at(14, 30).isoformat(),
                    "headline": "CPI inflation shock",
                    "symbols": ["SPY"],
                    "sentiment": -0.5,
                }
            ]
        ),
        encoding="utf-8",
    )
    assert score_headlines_before(fixture_settings, DAY, at(11, 0)) == 0.0
    assert score_headlines_before(fixture_settings, DAY, at(14, 29)) == 0.0
    assert score_headlines_before(fixture_settings, DAY, at(14, 30)) == -0.5
    assert score_headlines_before(fixture_settings, DAY, at(15, 30)) == -0.5
    assert score_headlines_before(fixture_settings, DAY, at(15, 31)) == 0.0  # 60-minute lookback

    replay = SessionReplay(fixture_settings, DAY)
    assert replay.context(at(11, 0), spot=450.0).macro_headline_score == 0.0
    assert replay.context(at(14, 45), spot=450.0).macro_headline_score == -0.5


def test_headline_without_timestamp_is_never_scored(fixture_settings):
    cache = Path(fixture_settings.backtest_cache_dir) / "macro_news" / f"{DAY.isoformat()}.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(["CPI inflation shock", {"headline": "tariff crisis"}]), encoding="utf-8")
    assert score_headlines_before(fixture_settings, DAY, at(15, 59)) == 0.0


def test_calendar_outcome_is_hidden_until_release(tmp_path, fixture_settings):
    calendar = tmp_path / "events.yaml"
    calendar.write_text(
        "events:\n"
        "  - name: FOMC\n"
        f"    release_at: '{at(14, 0).isoformat()}'\n"
        "    tier: 1\n"
        "    forecast: 5.25\n"
        "    actual: 5.50\n",
        encoding="utf-8",
    )
    fixture_settings.macro_calendar_path = str(calendar)
    replay = SessionReplay(fixture_settings, DAY)

    (before,) = replay.context(at(13, 59), spot=450.0).raw_by_provider["macro_news"]["events"]
    (after,) = replay.context(at(14, 0), spot=450.0).raw_by_provider["macro_news"]["events"]
    assert "actual" not in before
    assert before["forecast"] == 5.25  # the schedule and consensus are public beforehand
    assert after["actual"] == 5.50


def test_macro_blackout_not_active_before_release_window(tmp_path):
    calendar = tmp_path / "macro_events.yaml"
    calendar.write_text(
        f"events:\n  - name: CPI\n    release_at: '{at(13, 30).isoformat()}'\n",
        encoding="utf-8",
    )
    settings = Settings(
        BACKTEST_CACHE_DIR=str(tmp_path / "cache"),
        UW_BACKTEST_CACHE_DIR=str(tmp_path / "uw"),
        UW_SESSION_JSONL_DIR=str(tmp_path / "uw" / "sessions"),
        MACRO_CALENDAR_PATH=str(calendar),
        MACRO_BLACKOUT_BEFORE_MIN=30,
        MACRO_BLACKOUT_AFTER_MIN=45,
    )
    ctx = build_backtest_context(settings, day=DAY, spot=500, now=at(11, 0))
    assert ctx is not None
    assert ctx.macro_event_blackout is False
    assert ctx.as_of == at(11, 0)


# --- the past day's chain comes from that day, or not at all -------------------------------


def test_past_day_is_fetched_for_its_own_session_window_and_never_from_the_snapshot_endpoint(keyed_settings):
    alpaca = FakeAlpaca()
    loaded = load_session(keyed_settings, DAY, client=alpaca.client, now=after_close(LATER_DAY))

    assert loaded.missing is None and loaded.has_own_chain
    assert loaded.session.source == SOURCE_OPTION_BARS
    assert set(alpaca.paths()) == {"/v2/stocks/SPY/bars", "/v1beta1/options/bars"}
    assert not any("snapshot" in path for path in alpaca.paths())
    for request in alpaca.requests:
        start = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00"))
        assert (start, end) == (session_open(DAY), session_close(DAY))
    option_requests = [r for r in alpaca.requests if r.url.path == "/v1beta1/options/bars"]
    requested = {s for r in option_requests for s in r.url.params["symbols"].split(",")}
    assert requested and all(symbol.startswith("SPY240603") for symbol in requested)


def test_cache_for_a_date_holds_only_that_dates_contracts_and_bars(keyed_settings):
    alpaca = FakeAlpaca()
    load_session(keyed_settings, DAY, client=alpaca.client, now=after_close(LATER_DAY))

    cached = read_rows(chain_path(keyed_settings))
    assert cached and {row["symbol"][:9] for row in cached} == {"SPY240603"}
    for rows in (cached, read_rows(spot_cache_path(keyed_settings, "SPY", DAY))):
        stamps = [datetime.fromisoformat(row["t"].replace("Z", "+00:00")) for row in rows]
        assert min(stamps) >= session_open(DAY) and max(stamps) < session_close(DAY)
    assert audit_cache(keyed_settings.backtest_cache_dir) == []

    # A second run is served from the cache without touching the API.
    seen = len(alpaca.requests)
    again = load_session(keyed_settings, DAY, client=alpaca.client, now=after_close(LATER_DAY))
    assert again.missing is None and len(alpaca.requests) == seen


def test_http_error_is_not_cached_as_an_empty_chain(keyed_settings):
    failing = FakeAlpaca(option_status=500)
    loaded = load_session(keyed_settings, DAY, client=failing.client, now=after_close(LATER_DAY))
    assert (loaded.session, loaded.missing, loaded.has_own_chain) == (None, NO_CHAIN, False)
    assert not chain_path(keyed_settings).exists() and not chain_path(keyed_settings).with_suffix(".json").exists()

    # Nothing was remembered about the failure, so the next run asks again and succeeds.
    working = FakeAlpaca()
    retried = load_session(keyed_settings, DAY, client=working.client, now=after_close(LATER_DAY))
    assert retried.missing is None
    assert "/v1beta1/options/bars" in working.paths()


def test_stock_bar_error_caches_nothing_at_all(keyed_settings):
    failing = FakeAlpaca(stock_status=503)
    loaded = load_session(keyed_settings, DAY, client=failing.client, now=after_close(LATER_DAY))
    assert loaded.session is None and loaded.missing == NO_CHAIN
    assert cache_files(keyed_settings) == []


def test_day_with_no_option_history_is_an_explicit_no_chain(keyed_settings):
    """Alpaca answers 200 with no bars (e.g. before its option history starts)."""
    alpaca = FakeAlpaca(option_bars=False)
    loaded = load_session(keyed_settings, DAY, client=alpaca.client, now=after_close(LATER_DAY))
    assert (loaded.session, loaded.missing) == (None, NO_CHAIN)
    assert not chain_path(keyed_settings).exists()
    assert not any("snapshot" in path for path in alpaca.paths())


def test_no_keys_and_no_cache_is_no_chain_without_any_request(fixture_settings):
    alpaca = FakeAlpaca()
    loaded = load_session(fixture_settings, DAY, client=alpaca.client, now=after_close(LATER_DAY))
    assert (loaded.session, loaded.missing) == (None, NO_CHAIN)
    assert alpaca.requests == []


def test_session_still_in_progress_is_not_backtested(keyed_settings):
    alpaca = FakeAlpaca()
    loaded = load_session(keyed_settings, DAY, client=alpaca.client, now=session_open(DAY) + timedelta(hours=2))
    assert (loaded.session, loaded.missing) == (None, NOT_COMPLETED)
    assert alpaca.requests == [] and cache_files(keyed_settings) == []


# --- files left behind by the old loader ---------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "problem"),
    [
        # Today's snapshot filed under a past date: other-expiry contracts, no capture time.
        (snapshot_rows(LATER_DAY, snapshot_at=None), "no_capture_time"),
        (snapshot_rows(LATER_DAY, snapshot_at=session_open(DAY) + timedelta(hours=1)), "contracts_of_other_expiry"),
        # The right contracts, but quoted on a later day.
        (snapshot_rows(DAY, snapshot_at=session_open(LATER_DAY) + timedelta(hours=1)), "captured_on_2024-06-05"),
        (snapshot_rows(DAY, snapshot_at=session_close(DAY) + timedelta(hours=3)), "captured_outside_session"),
        # An HTTP error cached as an empty chain, and the junk row an empty response produced.
        ([], "empty_chain"),
        ([{"symbol": "snapshots", "strike": 0.0, "right": "C"}], "no_capture_time"),
    ],
)
def test_contaminated_chain_file_is_not_used_as_that_days_chain(fixture_settings, rows, problem):
    path = chain_path(fixture_settings)
    path.parent.mkdir(parents=True)
    path.with_suffix(".json").write_text(json.dumps(rows), encoding="utf-8")
    write_rows(spot_cache_path(fixture_settings, "SPY", DAY), spot_rows())

    assert load_chain_for_day(fixture_settings, ticker="SPY", day=DAY) == []
    loaded = load_session(fixture_settings, DAY, now=after_close(LATER_DAY))
    assert (loaded.session, loaded.missing, loaded.has_own_chain) == (None, NO_CHAIN, False)
    assert audit_cache(fixture_settings.backtest_cache_dir) == [(path.with_suffix(".json"), problem)]


def test_snapshot_captured_during_its_own_session_is_that_days_chain(fixture_settings):
    captured = session_open(DAY) + timedelta(hours=1)
    write_rows(chain_path(fixture_settings), snapshot_rows(DAY, snapshot_at=captured))
    write_rows(spot_cache_path(fixture_settings, "SPY", DAY), spot_rows())

    loaded = load_session(fixture_settings, DAY, now=after_close(LATER_DAY))
    assert loaded.missing is None and loaded.session.source == SOURCE_CHAIN_SNAPSHOT
    assert loaded.session.snapshot_at == captured
    assert loaded.session.chain_at(captured - timedelta(minutes=1)) == []  # not visible before capture
    assert audit_cache(fixture_settings.backtest_cache_dir) == []


def test_daily_bar_is_not_used_as_intraday_spot(fixture_settings):
    """The old spot loader cached one daily bar per date; its close is the 16:00 price."""
    midnight = datetime(DAY.year, DAY.month, DAY.day, tzinfo=ET).astimezone(timezone.utc).isoformat()
    spot_path = spot_cache_path(fixture_settings, "SPY", DAY)
    write_rows(spot_path, [{"t": midnight, "o": 448.0, "h": 452.0, "l": 447.0, "c": 451.0, "v": 5e7}])
    write_rows(chain_path(fixture_settings), snapshot_rows(DAY, snapshot_at=session_open(DAY) + timedelta(hours=1)))

    loaded = load_session(fixture_settings, DAY, now=after_close(LATER_DAY))
    assert (loaded.session, loaded.missing, loaded.has_own_chain) == (None, NO_SPOT, True)
    assert audit_cache(fixture_settings.backtest_cache_dir) == [(spot_path, "no_intraday_bars")]


def test_live_snapshot_is_only_filed_under_its_capture_date(fixture_settings):
    candidate = OptionCandidate(symbol="SPY", strike=445.0, right="P", expiry="20240605", bid=0.39, ask=0.41)
    captured = session_open(LATER_DAY) + timedelta(hours=1)
    with pytest.raises(ValueError, match="cannot be stored as 2024-06-03"):
        save_chain_json(
            [candidate], base_dir=fixture_settings.backtest_cache_dir, ticker="SPY", day=DAY, snapshot_at=captured
        )
    assert cache_files(fixture_settings) == []

    saved = save_chain_json([candidate], base_dir=fixture_settings.backtest_cache_dir, ticker="SPY", snapshot_at=captured)
    assert saved.stem == LATER_DAY.isoformat()


def test_cache_audit_purge_removes_only_the_bad_files(fixture_settings, capsys):
    good = write_rows(chain_path(fixture_settings), snapshot_rows(DAY, snapshot_at=session_open(DAY) + timedelta(hours=1)))
    bad = write_rows(chain_path(fixture_settings, LATER_DAY), snapshot_rows(date(2026, 11, 16), snapshot_at=None))
    headlines = Path(fixture_settings.backtest_cache_dir) / "macro_news" / f"{DAY.isoformat()}.json"
    headlines.parent.mkdir(parents=True)
    headlines.write_text("[]", encoding="utf-8")

    cache_audit_main(["--cache-dir", fixture_settings.backtest_cache_dir])
    assert bad.exists() and f"suspect\tno_capture_time\t{bad}" in capsys.readouterr().out

    cache_audit_main(["--cache-dir", fixture_settings.backtest_cache_dir, "--purge"])
    assert not bad.exists() and good.exists() and headlines.exists()
