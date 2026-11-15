"""Tier-1 event blackout: live provider, backtest replay and the strategy gate agree."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from odte_spy_ibkr.backtest.replay import SessionReplay
from odte_spy_ibkr.backtest.simulator import simulate_session
from odte_spy_ibkr.config import CreditSide
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.providers.macro_news import MacroNewsProvider, tier1_blackout_active
from odte_spy_ibkr.strategy.interface import OpenPosition, manage_open, propose_entry
from odte_spy_ibkr.strategy.macro_gate import MacroDecision, evaluate_macro_gate

from conftest import FIXTURE_DAY, build_session, constant

ET = ZoneInfo("America/New_York")
DAY = FIXTURE_DAY
LEGS = {("P", 445.0): constant(0.40), ("P", 444.0): constant(0.15)}


def at(hour: int, minute: int) -> datetime:
    return datetime.combine(DAY, time(hour, minute), tzinfo=ET)


def write_calendar(settings, events: list[dict]) -> None:
    lines = ["events:"]
    for evt in events:
        lines.append(f"  - name: {evt['name']}")
        lines.append(f"    release_at: '{evt['release_at']}'")
        if "tier" in evt:
            lines.append(f"    tier: {evt['tier']}")
    Path(settings.macro_calendar_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(13, 29), False),  # 31 minutes before
        (at(13, 30), True),  # window opens 30 minutes before
        (at(14, 0), True),
        (at(14, 45), True),  # window closes 45 minutes after
        (at(14, 46), False),
    ],
)
def test_blackout_window_boundaries(fixture_settings, now, expected):
    events = [{"name": "FOMC", "release_at": at(14, 0).isoformat(), "tier": 1}]
    assert tier1_blackout_active(events, now, fixture_settings) is expected


def test_blackout_window_is_configurable(fixture_settings):
    fixture_settings.macro_tier1_blackout_before_min = 5
    fixture_settings.macro_tier1_blackout_after_min = 10
    events = [{"name": "CPI", "release_at": at(8, 30).isoformat(), "tier": 1}]
    assert not tier1_blackout_active(events, at(8, 24), fixture_settings)
    assert tier1_blackout_active(events, at(8, 25), fixture_settings)
    assert tier1_blackout_active(events, at(8, 40), fixture_settings)
    assert not tier1_blackout_active(events, at(8, 41), fixture_settings)


def test_only_tier1_events_black_out(fixture_settings):
    release = at(10, 0).isoformat()
    assert not tier1_blackout_active([{"name": "ISM", "release_at": release, "tier": 2}], at(10, 0), fixture_settings)
    assert tier1_blackout_active([{"name": "CPI", "release_at": release}], at(10, 0), fixture_settings)  # tier defaults to 1


def test_naive_release_time_is_read_as_eastern(fixture_settings):
    events = [{"name": "CPI", "release_at": "2024-06-03T08:30:00", "tier": 1}]
    assert tier1_blackout_active(events, at(8, 30), fixture_settings)
    assert not tier1_blackout_active(events, at(12, 30), fixture_settings)


def test_event_on_another_day_does_not_black_out_today(fixture_settings):
    write_calendar(fixture_settings, [{"name": "CPI", "release_at": (at(11, 0) + timedelta(days=1)).isoformat()}])
    replay = SessionReplay(fixture_settings, DAY)
    assert replay.context(at(11, 0), spot=450.0).macro_event_blackout is False


def test_gate_blocks_entries_in_blackout(fixture_settings):
    result = evaluate_macro_gate(fixture_settings, MarketContext(macro_event_blackout=True))
    assert (result.decision, result.reason) == (MacroDecision.BLOCK, "tier1_blackout")

    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    chain = session.chain_at(at(11, 0))
    ctx = MarketContext(spot=450.0, macro_event_blackout=True, as_of=at(11, 0))
    assert propose_entry(fixture_settings, ctx, chain, now=at(11, 0)) is None
    ctx.macro_event_blackout = False
    assert propose_entry(fixture_settings, ctx, chain, now=at(11, 0)) is not None


def test_macro_disabled_ignores_blackout(fixture_settings):
    fixture_settings.macro_enabled = False
    result = evaluate_macro_gate(fixture_settings, MarketContext(macro_event_blackout=True))
    assert result.decision == MacroDecision.ALLOW

    write_calendar(fixture_settings, [{"name": "FOMC", "release_at": at(11, 0).isoformat(), "tier": 1}])
    assert SessionReplay(fixture_settings, DAY).context(at(11, 0), spot=450.0).macro_event_blackout is False


def test_backtest_opens_no_trade_inside_the_blackout(fixture_settings):
    """Blackout 09:30-10:45 around a 10:00 release: the 10:00 entry is deferred to 10:50."""
    write_calendar(fixture_settings, [{"name": "ISM", "release_at": at(10, 0).isoformat(), "tier": 1}])
    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(fixture_settings, session)
    assert sim.trades[0].opened_at == at(10, 50)


def test_open_put_credit_is_flattened_when_blackout_starts(fixture_settings):
    """Entry at 10:00, FOMC at 14:00: the position is closed at 13:30 and not reopened
    until the window ends, after the entry cutoff."""
    write_calendar(fixture_settings, [{"name": "FOMC", "release_at": at(14, 0).isoformat(), "tier": 1}])
    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(fixture_settings, session)

    first = sim.trades[0]
    assert (first.opened_at, first.closed_at, first.exit_reason) == (at(10, 0), at(13, 30), "signal_exit")
    assert [tr.opened_at for tr in sim.trades[1:]] == [at(14, 50)]

    position = OpenPosition(
        spread=propose_entry(
            fixture_settings,
            MarketContext(spot=450.0, as_of=at(10, 0)),
            session.chain_at(at(10, 0)),
            now=at(10, 0),
        ).spread,
        side=CreditSide.PUT,
        contracts=1,
        entry_credit=0.24,
        opened_at=at(10, 0),
    )
    blackout = MarketContext(spot=450.0, macro_event_blackout=True)
    assert manage_open(fixture_settings, position, blackout, now=at(13, 30)) == "flatten"
    assert manage_open(fixture_settings, position, MarketContext(spot=450.0), now=at(13, 30)) == "hold"


def test_backtest_reads_cached_calendar_before_yaml(fixture_settings):
    cache = Path(fixture_settings.backtest_cache_dir) / "macro_calendar" / f"{DAY.year}.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps({"year": DAY.year, "events": [{"name": "NFP", "release_at": at(8, 30).isoformat(), "tier": 1}]}),
        encoding="utf-8",
    )
    replay = SessionReplay(fixture_settings, DAY)
    assert replay.context(at(9, 0), spot=450.0).macro_event_blackout is True
    assert replay.context(at(9, 16), spot=450.0).macro_event_blackout is False


def test_live_provider_blackout_matches_calendar(fixture_settings):
    write_calendar(
        fixture_settings,
        [
            {"name": "FOMC", "release_at": at(14, 0).isoformat(), "tier": 1},
            {"name": "ISM", "release_at": at(10, 0).isoformat(), "tier": 2},
        ],
    )
    fixture_settings.providers_enabled = "macro_news"
    provider = MacroNewsProvider(fixture_settings)

    def blackout(now: datetime) -> bool:
        return provider._fetch(MarketContext(as_of=now)).macro_event_blackout

    assert blackout(at(10, 0)) is False  # tier 2
    assert blackout(at(13, 45)) is True
    assert blackout(at(15, 0)) is False
