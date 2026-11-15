from __future__ import annotations

import json
from datetime import date

from odte_spy_ibkr.backtest.ledger import DailyLedger
from odte_spy_ibkr.backtest.simulator import simulate_day, simulate_entry_credit
from odte_spy_ibkr.config import CreditSide, Settings, UWMode
from odte_spy_ibkr.strategy.interface import OpenPosition, propose_entry
from odte_spy_ibkr.backtest.data_loader import _rows_to_candidates
from odte_spy_ibkr.backtest.replay import build_backtest_context
from odte_spy_ibkr.chain import build_chain_skew, today_expiry
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _fixture_rows(spot: float = 450.0) -> list[dict]:
    rows = []
    center = int(spot)
    for strike in range(center - 8, center + 1):
        otm = max(0.0, spot - strike)
        bid = max(0.08, 0.72 - otm * 0.06)
        rows.append(
            {
                "symbol": f"SPY240603P{strike}",
                "strike": float(strike),
                "right": "P",
                "bid": bid,
                "ask": bid + 0.04,
                "delta": min(0.35, 0.08 + otm * 0.012),
                "volume": 200,
                "open_interest": 800,
            }
        )
    puts = {r["strike"]: r for r in rows if r["right"] == "P"}
    for strike, short in puts.items():
        long_row = puts.get(strike - 1)
        if long_row is not None:
            long_row["ask"] = max(0.05, float(short["bid"]) - 0.18)
    return rows


def test_e2e_open_close_matches_hand_pnl(tmp_path):
    day = date(2024, 6, 3)
    macro_path = tmp_path / "macro.yaml"
    macro_path.write_text(
        'events:\n  - name: CPI\n    release_at: "2024-06-04T11:00:00-04:00"\n    tier: 1\n',
        encoding="utf-8",
    )
    settings = Settings(
        BACKTEST_CACHE_DIR=str(tmp_path / "cache"),
        UW_BACKTEST_CACHE_DIR=str(tmp_path / "uw"),
        uw_mode=UWMode.ALERTS_ONLY,
        credit_side=CreditSide.PUT,
        min_credit=0.10,
        spread_width=1.0,
        short_delta_min=0.10,
        short_delta_max=0.20,
        macro_enabled=True,
        macro_calendar_path=str(macro_path),
    )
    spot = 450.0
    (tmp_path / "uw").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uw" / f"{day.isoformat()}.json").write_text(
        json.dumps({"aggregate_bias": 0.25}),
        encoding="utf-8",
    )
    chain = _rows_to_candidates(_fixture_rows(spot), day)
    from datetime import datetime

    signal_at = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=11, minute=0)
    skew = build_chain_skew(chain, expiry=today_expiry(day), spot=spot, as_of=signal_at)
    ctx = build_backtest_context(settings, day=day, spot=spot, chain_skew=skew, now=signal_at)
    intent = propose_entry(settings, ctx, chain)
    assert intent is not None

    sim1 = simulate_day(settings, day=day, candidates=chain, spot=spot, open_position=None)
    assert sim1.traded and sim1.open_position is None  # EOD flatten same day for 0DTE
    pnl1 = sim1.pnl
    expected_entry = simulate_entry_credit(intent, settings)
    slip = settings.backtest_slippage_ticks * 0.01
    per_share = expected_entry / (intent.contracts * 100)
    expected_exit = per_share * intent.contracts * 100 * 0.3 - slip
    assert abs(pnl1 - (expected_entry + expected_exit)) < 1e-6

    # Day 2: independent session (position must not carry overnight)
    day2 = date(2024, 6, 4)
    chain2 = _rows_to_candidates(_fixture_rows(spot), day2)
    sim2 = simulate_day(settings, day=day2, candidates=chain2, spot=spot, open_position=None)
    assert sim2.open_position is None
    # CPI tier-1 blackout at 11:00 can block new entry — still must not hold overnight
    assert "no_entry" in sim2.session_reason or "entry_opened" in sim2.session_reason

    ledger = DailyLedger()
    ledger.record(day, pnl1)
    ledger.record(day2, sim2.pnl)
    assert ledger.total_pnl() > 0