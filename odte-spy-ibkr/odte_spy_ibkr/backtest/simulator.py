from __future__ import annotations

from datetime import date, datetime

from odte_spy_ibkr.backtest.replay import build_backtest_context
from odte_spy_ibkr.chain import build_chain_skew, today_expiry
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.strategy.interface import OpenPosition, SpreadIntent, manage_open, propose_entry


def simulate_entry_credit(
    intent: SpreadIntent,
    settings: Settings,
) -> float:
    credit = intent.spread.estimated_credit or settings.min_credit
    slip = settings.backtest_slippage_ticks * 0.01
    commission = 2 * settings.backtest_commission_per_contract * intent.contracts
    return credit * intent.contracts * 100 - slip - commission


def simulate_day(
    settings: Settings,
    *,
    day: date,
    candidates,
    spot: float,
    open_position: OpenPosition | None,
) -> tuple[float, OpenPosition | None, bool]:
    skew = build_chain_skew(candidates, expiry=today_expiry(day), spot=spot, as_of=datetime.combine(day, datetime.min.time()))
    ctx = build_backtest_context(settings, day=day, spot=spot, chain_skew=skew)
    if ctx is None:
        return 0.0, open_position, False

    pnl = 0.0
    traded = False
    if open_position is None:
        intent = propose_entry(settings, ctx, candidates)
        if intent is not None:
            credit = simulate_entry_credit(intent, settings)
            open_position = OpenPosition(
                spread=intent.spread,
                side=intent.side,
                contracts=intent.contracts,
                entry_credit=credit / (intent.contracts * 100),
                opened_at=datetime.combine(day, datetime.min.time()),
            )
            pnl += credit
            traded = True
    else:
        action = manage_open(settings, open_position, ctx)
        if action in {"flatten", "stop"}:
            exit_cost = settings.backtest_slippage_ticks * 0.01
            hold_pnl = (open_position.entry_credit * open_position.contracts * 100) * 0.3 - exit_cost
            pnl += hold_pnl if action == "flatten" else -hold_pnl
            open_position = None
            traded = True
    return pnl, open_position, traded