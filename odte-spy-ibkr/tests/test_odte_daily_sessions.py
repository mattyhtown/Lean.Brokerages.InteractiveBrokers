from __future__ import annotations

from datetime import date

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.backtest.data_loader import load_chain_for_day
from odte_spy_ibkr.backtest.simulator import simulate_day
from odte_spy_ibkr.backtest.spot_loader import load_spot_for_day
from odte_spy_ibkr.config import Settings, UWBacktestMode


def test_odte_positions_do_not_carry_overnight_default_config():
    settings = Settings(
        UW_BACKTEST_MODE=UWBacktestMode.NEUTRAL,
        macro_enabled=False,
    )
    start, end = date(2024, 6, 1), date(2024, 6, 30)
    days = iter_trading_days(start, end)
    assert len(days) >= 15
    open_pos = None
    entry_days = 0
    for day in days:
        spot = load_spot_for_day(settings, ticker="SPY", day=day)
        chain = load_chain_for_day(settings, ticker="SPY", day=day, spot=spot)
        sim = simulate_day(settings, day=day, candidates=chain, spot=spot, open_position=open_pos)
        open_pos = sim.open_position
        assert open_pos is None, f"0DTE position carried past session on {day} reason={sim.session_reason}"
        if "entry_opened" in sim.session_reason:
            entry_days += 1
    assert entry_days >= 10, f"expected daily entries on synthetic data, got {entry_days}"