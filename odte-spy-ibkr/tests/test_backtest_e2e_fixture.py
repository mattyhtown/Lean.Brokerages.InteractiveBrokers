"""Fill model and daily ledger, checked against hand-computed PnL on a fixture session."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from odte_spy_ibkr.backtest.ledger import DailyLedger
from odte_spy_ibkr.backtest.simulator import session_limits, simulate_session
from odte_spy_ibkr.backtest.calendar import is_early_close

from conftest import FIXTURE_DAY, build_session, constant

ET = ZoneInfo("America/New_York")

# Short 445P / long 444P, $1 wide, 1 contract.
# Entry at 10:00 (first decision after the 30-minute liquidity window):
#   mid credit 0.40 - 0.15 = 0.25, minus 1 tick slippage -> 0.24
# Commission: 0.65 * 1 contract * 2 legs = 1.30 per side.
LEGS = {("P", 445.0): constant(0.40), ("P", 444.0): constant(0.15)}


def at(hour: int, minute: int, day: date = FIXTURE_DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET)


def test_time_exit_matches_hand_pnl(fixture_settings):
    def short_leg(t):
        return 0.40 if t < at(12, 0) else 0.10

    def long_leg(t):
        return 0.15 if t < at(12, 0) else 0.04

    session = build_session(
        fixture_settings,
        spot=constant(450.0),
        legs={("P", 445.0): short_leg, ("P", 444.0): long_leg},
    )
    sim = simulate_session(fixture_settings, session)

    assert sim.session_reason == "entry_opened;time_exit"
    (trade,) = sim.trades
    assert trade.opened_at == at(10, 0)
    assert trade.closed_at == at(15, 45)
    assert (trade.short_strike, trade.long_strike, trade.contracts) == (445.0, 444.0, 1)
    assert trade.entry_credit == pytest.approx(0.24)
    # Exit: mid 0.10 - 0.04 = 0.06, plus 1 tick slippage -> 0.07 debit.
    assert trade.exit_debit == pytest.approx(0.07)
    assert trade.commission == pytest.approx(2.60)
    # (0.24 - 0.07) * 100 - 2.60 = 14.40
    assert trade.pnl == pytest.approx(14.40)
    assert sim.pnl == pytest.approx(14.40)
    assert sim.open_position is None


def test_stop_on_spread_mark(fixture_settings):
    fixture_settings.stop_loss_credit_multiple = 2.0

    def short_leg(t):
        return 0.40 if t < at(11, 0) else 0.80

    session = build_session(
        fixture_settings,
        spot=constant(450.0),
        legs={("P", 445.0): short_leg, ("P", 444.0): constant(0.15)},
    )
    sim = simulate_session(fixture_settings, session)

    first = sim.trades[0]
    # Mark 0.80 - 0.15 = 0.65 >= 2 * 0.24, first visible at 11:00.
    assert first.exit_reason == "stop"
    assert first.closed_at == at(11, 0)
    assert first.exit_debit == pytest.approx(0.66)
    # (0.24 - 0.66) * 100 - 2.60 = -44.60
    assert first.pnl == pytest.approx(-44.60)


def test_stop_is_off_by_default(fixture_settings):
    assert fixture_settings.stop_loss_credit_multiple == 0.0

    def short_leg(t):
        return 0.40 if t < at(11, 0) else 0.80

    session = build_session(
        fixture_settings,
        spot=constant(450.0),
        legs={("P", 445.0): short_leg, ("P", 444.0): constant(0.15)},
    )
    sim = simulate_session(fixture_settings, session)
    assert [tr.exit_reason for tr in sim.trades] == ["time_exit"]


def test_held_to_expiry_settles_at_intrinsic_without_exit_commission(fixture_settings):
    fixture_settings.flatten_time = time(16, 0)  # never flatten: hold to expiry

    def spot(t):
        return 450.0 if t < at(15, 0) else 443.0

    session = build_session(fixture_settings, spot=spot, legs=LEGS)
    sim = simulate_session(fixture_settings, session)

    (trade,) = sim.trades
    assert trade.exit_reason == "expiry_settlement"
    assert trade.closed_at == at(16, 0)
    assert trade.exit_debit == pytest.approx(1.0)  # full width: spot settled below the long strike
    assert trade.commission == pytest.approx(1.30)  # entry side only
    # (0.24 - 1.00) * 100 - 1.30 = -77.30
    assert trade.pnl == pytest.approx(-77.30)


def test_exit_uses_model_reprice_when_legs_stop_trading(fixture_settings):
    """No prints after 10:30: the exit mark is repriced from each leg's last implied vol
    at the current spot, not taken from the stale 10:30 prices."""

    def traded_until_1030(price):
        return lambda t: price if t <= at(10, 30) else None

    def spot(t):
        return 450.0 if t <= at(10, 30) else 430.0

    session = build_session(
        fixture_settings,
        spot=spot,
        legs={("P", 445.0): traded_until_1030(0.40), ("P", 444.0): traded_until_1030(0.15)},
    )
    assert session.chain_at(at(11, 0)) == []  # stale legs are not offered for entry
    sim = simulate_session(fixture_settings, session)

    (trade,) = sim.trades
    assert trade.exit_reason == "time_exit"
    assert trade.exit_debit == pytest.approx(1.0, abs=1e-6)  # deep in the money: full width
    assert trade.pnl == pytest.approx(-78.60, abs=1e-3)


def test_entry_rejected_when_slippage_takes_credit_below_minimum(fixture_settings):
    session = build_session(
        fixture_settings,
        spot=constant(450.0),
        # Chain credit 0.34 - 0.16 = 0.18 passes MIN_CREDIT, mid 0.20 less 6 ticks does not.
        legs={("P", 445.0): constant(0.35), ("P", 444.0): constant(0.15)},
    )
    fixture_settings.backtest_slippage_ticks = 6.0
    sim = simulate_session(fixture_settings, session)
    assert not sim.traded
    assert sim.session_reason == "no_entry:min_credit_after_slippage"
    assert sim.pnl == 0.0


def test_daily_loss_limit_stops_reentry(fixture_settings):
    fixture_settings.stop_loss_credit_multiple = 2.0
    fixture_settings.max_daily_loss = 40.0

    def short_leg(t):
        # Stopped at 11:00, then the chain looks attractive again from 12:00.
        if t < at(11, 0):
            return 0.40
        return 0.80 if t < at(12, 0) else 0.40

    session = build_session(
        fixture_settings,
        spot=constant(450.0),
        legs={("P", 445.0): short_leg, ("P", 444.0): constant(0.15)},
    )
    sim = simulate_session(fixture_settings, session)
    assert len(sim.trades) == 1
    assert sim.pnl == pytest.approx(-44.60)


def test_no_position_is_carried_past_the_session(fixture_settings):
    session = build_session(fixture_settings, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(fixture_settings, session)
    assert sim.traded and sim.open_position is None
    assert all(tr.closed_at <= session.close_at for tr in sim.trades)


def test_half_day_moves_cutoff_and_flatten_with_the_close(fixture_settings):
    half_day = date(2024, 11, 29)  # day after Thanksgiving, 13:00 close
    assert is_early_close(half_day)
    limits = session_limits(fixture_settings, half_day)
    assert limits.entry_cutoff == time(12, 0)
    assert limits.flatten_time == time(12, 45)

    session = build_session(fixture_settings, day=half_day, spot=constant(450.0), legs=LEGS)
    sim = simulate_session(fixture_settings, session)
    (trade,) = sim.trades
    assert trade.exit_reason == "time_exit"
    assert trade.closed_at == at(12, 45, half_day)


def test_ledger_returns_use_start_of_day_equity():
    d1, d2, d3 = date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5)
    ledger = DailyLedger(equity_base=10_000.0)
    ledger.record(d1, 100.0)
    ledger.record(d1, 50.0)  # two trades on one day add up
    ledger.record(d3, -303.0)
    days = [d1, d2, d3]
    assert ledger.daily_pnl_series(days) == [150.0, 0.0, -303.0]
    assert ledger.daily_return_series(days) == pytest.approx([0.015, 0.0, -303.0 / 10_150.0])
    assert ledger.total_pnl() == pytest.approx(-153.0)
