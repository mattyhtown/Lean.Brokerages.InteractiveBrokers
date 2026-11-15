from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from odte_spy_ibkr.backtest.replay import build_backtest_context
from odte_spy_ibkr.chain import build_chain_skew, today_expiry
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.risk.limits import RiskLimits, should_flatten
from odte_spy_ibkr.strategy.interface import OpenPosition, SpreadIntent, manage_open, propose_entry


def simulate_entry_credit(
    intent: SpreadIntent,
    settings: Settings,
) -> float:
    credit = intent.spread.estimated_credit or settings.min_credit
    slip = settings.backtest_slippage_ticks * 0.01
    commission = 2 * settings.backtest_commission_per_contract * intent.contracts
    return credit * intent.contracts * 100 - slip - commission


@dataclass(frozen=True)
class DaySimulation:
    pnl: float
    open_position: OpenPosition | None
    traded: bool
    session_reason: str


def _flatten_pnl(open_position: OpenPosition, settings: Settings, *, action: str) -> float:
    exit_cost = settings.backtest_slippage_ticks * 0.01
    notional = open_position.entry_credit * open_position.contracts * 100
    hold_pnl = notional * 0.3 - exit_cost
    return hold_pnl if action == "flatten" else -hold_pnl


def simulate_day(
    settings: Settings,
    *,
    day: date,
    candidates,
    spot: float,
    open_position: OpenPosition | None,
) -> DaySimulation:
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    signal_at = datetime.combine(day, datetime.min.time(), tzinfo=et).replace(hour=11, minute=0)
    skew = build_chain_skew(candidates, expiry=today_expiry(day), spot=spot, as_of=signal_at)
    reason_parts: list[str] = []
    had_open = open_position is not None

    # 0DTE cannot be held overnight; flatten carry-over from older backtest runs / bugs.
    if open_position is not None and open_position.opened_at.date() < day:
        pnl = _flatten_pnl(open_position, settings, action="flatten")
        reason_parts.append("stale_odte_carryover_flatten")
        return DaySimulation(pnl=pnl, open_position=None, traded=True, session_reason=reason_parts[0])

    ctx = build_backtest_context(settings, day=day, spot=spot, chain_skew=skew, now=signal_at)
    if ctx is None:
        reason = "uw_skip_session"
        if had_open:
            reason = "position_open_uw_skip_session"
        return DaySimulation(pnl=0.0, open_position=open_position, traded=False, session_reason=reason)

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
                opened_at=signal_at,
            )
            pnl += credit
            traded = True
            reason_parts.append("entry_opened")
        else:
            from odte_spy_ibkr.backtest.diagnostics import explain_no_entry

            reason_parts.append(f"no_entry:{explain_no_entry(settings, ctx, candidates)}")
    else:
        action = manage_open(settings, open_position, ctx, now=signal_at)
        if action in {"flatten", "stop"}:
            pnl += _flatten_pnl(open_position, settings, action=action)
            open_position = None
            traded = True
            reason_parts.append(f"intraday_{action}")
        else:
            reason_parts.append("position_open_manage_hold")

    flatten_at = datetime.combine(day, settings.flatten_time, tzinfo=et)
    limits = RiskLimits(
        max_contracts=settings.max_contracts,
        max_daily_loss=settings.max_daily_loss,
        entry_cutoff=settings.entry_cutoff,
        flatten_time=settings.flatten_time,
        min_minutes_after_open=settings.min_minutes_after_open,
    )
    if open_position is not None and should_flatten(limits, now=flatten_at):
        ctx_eod = build_backtest_context(settings, day=day, spot=spot, chain_skew=skew, now=flatten_at)
        action = "hold"
        if ctx_eod is not None:
            action = manage_open(settings, open_position, ctx_eod, now=flatten_at)
        if action == "hold":
            action = "flatten"
        pnl += _flatten_pnl(open_position, settings, action=action)
        open_position = None
        traded = True
        reason_parts.append("eod_flatten_0dte")

    session_reason = ";".join(reason_parts) if reason_parts else ("no_activity" if not traded else "traded")
    return DaySimulation(pnl=pnl, open_position=open_position, traded=traded, session_reason=session_reason)