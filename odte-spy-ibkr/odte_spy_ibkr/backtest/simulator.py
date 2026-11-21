"""Minute-driven replay of one 0DTE session through the shared strategy interface.

Fill model
- Entry: credit = spread mid (short mid - long mid) - BACKTEST_SLIPPAGE_TICKS * $0.01,
  rejected below MIN_CREDIT.
- Exit: debit = spread mid + the same slippage, clamped to [0, width]. Triggers are the
  time exit (FLATTEN_TIME, moved earlier on half days), the optional stop on the spread
  mark, and the strategy's signal exits. A position still open at the close settles at
  intrinsic value with no exit commission.
- Commission: BACKTEST_COMMISSION_PER_CONTRACT per contract per leg, at entry and exit.
- PnL = (credit - debit) * 100 * contracts - commissions, realized on the session date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from odte_spy_ibkr.backtest.calendar import ET, REGULAR_CLOSE, session_close
from odte_spy_ibkr.backtest.diagnostics import explain_no_entry
from odte_spy_ibkr.backtest.ledger import TradeRecord
from odte_spy_ibkr.backtest.replay import ENTRY_BLOCK_NOTE, SessionReplay
from odte_spy_ibkr.backtest.session_data import SessionData
from odte_spy_ibkr.chain import VerticalSpreadSelection, build_chain_skew, spread_mid, today_expiry
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.risk.limits import RiskDecision, RiskLimits, RiskState, check_new_entry, should_flatten
from odte_spy_ibkr.strategy.interface import OpenPosition, manage_open, propose_entry

TICK = 0.01
CONTRACT_MULTIPLIER = 100
LEGS = 2


def entry_credit_after_slippage(spread: VerticalSpreadSelection, settings: Settings) -> float | None:
    mid = spread_mid(spread)
    if mid is None:
        return None
    return mid - settings.backtest_slippage_ticks * TICK


def exit_debit_after_slippage(mark: float, spread: VerticalSpreadSelection, settings: Settings) -> float:
    return min(spread.width, max(0.0, mark) + settings.backtest_slippage_ticks * TICK)


def side_commission(settings: Settings, contracts: int) -> float:
    return settings.backtest_commission_per_contract * contracts * LEGS


def trade_pnl(entry_credit: float, exit_debit: float, contracts: int, commission: float) -> float:
    return (entry_credit - exit_debit) * CONTRACT_MULTIPLIER * contracts - commission


def session_limits(settings: Settings, day: date) -> RiskLimits:
    """Risk limits for `day`; on a half day the cutoff and flatten times move up with the close."""
    shift = datetime.combine(day, REGULAR_CLOSE, tzinfo=ET) - session_close(day)

    def shifted(t):
        return (datetime.combine(day, t) - shift).time()

    return RiskLimits(
        max_contracts=settings.max_contracts,
        max_daily_loss=settings.max_daily_loss,
        entry_cutoff=shifted(settings.entry_cutoff),
        flatten_time=shifted(settings.flatten_time),
        min_minutes_after_open=settings.min_minutes_after_open,
    )


@dataclass(frozen=True)
class DaySimulation:
    pnl: float
    open_position: OpenPosition | None
    traded: bool
    session_reason: str
    trades: tuple[TradeRecord, ...] = ()
    scored: bool = True
    source: str = ""


def simulate_session(settings: Settings, session: SessionData, *, contracts: int = 1) -> DaySimulation:
    day = session.day
    replay = SessionReplay(settings, day)
    if replay.skip_reason is not None:
        return DaySimulation(0.0, None, False, replay.skip_reason, scored=False, source=session.source)

    limits = session_limits(settings, day)
    expiry = today_expiry(day)
    interval = max(1, settings.backtest_decision_interval_min)
    position: OpenPosition | None = None
    entry_commission = 0.0
    realized = 0.0
    trades: list[TradeRecord] = []
    risk_reason = "no_decision_time"
    strategy_reason: str | None = None

    def close_position(at: datetime, debit: float, commission: float, reason: str) -> None:
        nonlocal position, realized
        assert position is not None
        total_commission = entry_commission + commission
        pnl = trade_pnl(position.entry_credit, debit, position.contracts, total_commission)
        trades.append(
            TradeRecord(
                day=day,
                side=position.side.value,
                short_strike=position.spread.short_leg.strike,
                long_strike=position.spread.long_leg.strike,
                contracts=position.contracts,
                opened_at=position.opened_at,
                closed_at=at,
                entry_credit=position.entry_credit,
                exit_debit=debit,
                commission=total_commission,
                pnl=pnl,
                exit_reason=reason,
            )
        )
        realized += pnl
        position = None

    t = session.open_at
    minute = 0
    while t < session.close_at - timedelta(minutes=1):
        t += timedelta(minutes=1)
        minute += 1
        spot = session.spot_at(t)
        if spot is None:
            continue

        if position is not None:
            mark = session.spread_mark(position.spread, t)
            if should_flatten(limits, now=t):
                action, reason = "flatten", "time_exit"
            else:
                skew = build_chain_skew(session.chain_at(t, greeks=False), expiry=expiry, spot=spot, as_of=t)
                ctx = replay.context(t, spot=spot, chain_skew=skew)
                action = manage_open(settings, position, ctx, now=t, spread_mark=mark)
                reason = "stop" if action == "stop" else "signal_exit"
            if action != "hold" and mark is not None:
                debit = exit_debit_after_slippage(mark, position.spread, settings)
                close_position(t, debit, side_commission(settings, position.contracts), reason)
            continue

        if minute % interval:
            continue
        decision, why = check_new_entry(limits, RiskState(realized_pnl=realized), now=t)
        if decision == RiskDecision.BLOCK:
            risk_reason = f"risk:{why}"
            continue
        chain = session.chain_at(t)
        skew = build_chain_skew(chain, expiry=expiry, spot=spot, as_of=t)
        ctx = replay.context(t, spot=spot, chain_skew=skew)
        if ENTRY_BLOCK_NOTE in ctx.provider_notes:
            strategy_reason = ctx.provider_notes[ENTRY_BLOCK_NOTE]
            continue
        intent = propose_entry(settings, ctx, chain, contracts=contracts, now=t)
        if intent is None:
            strategy_reason = explain_no_entry(settings, ctx, chain)
            continue
        credit = entry_credit_after_slippage(intent.spread, settings)
        if credit is None or credit < settings.min_credit:
            strategy_reason = "min_credit_after_slippage"
            continue
        position = OpenPosition(
            spread=intent.spread,
            side=intent.side,
            contracts=intent.contracts,
            entry_credit=credit,
            opened_at=t,
        )
        entry_commission = side_commission(settings, intent.contracts)

    if position is not None:
        # No tradable mark before the close: the spread expires and settles at intrinsic.
        settle = session.settlement_value(position.spread)
        close_position(session.close_at, settle if settle is not None else position.spread.width, 0.0, "expiry_settlement")

    if trades:
        session_reason = "entry_opened;" + ";".join(tr.exit_reason for tr in trades)
    else:
        # The last reason the strategy itself gave; the clock-based risk blocks only when
        # the strategy was never consulted.
        session_reason = f"no_entry:{strategy_reason or risk_reason}"
    return DaySimulation(
        pnl=realized,
        open_position=None,
        traded=bool(trades),
        session_reason=session_reason,
        trades=tuple(trades),
        source=session.source,
    )
