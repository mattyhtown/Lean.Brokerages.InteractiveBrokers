from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo

from odte_spy_ibkr.chain import (
    OptionCandidate,
    VerticalSpreadSelection,
    net_call_risk_notional,
    net_call_risk_within_limit,
)
from odte_spy_ibkr.config import CreditSide, Settings
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.strategy.credit_spread import build_entry_plan
from odte_spy_ibkr.strategy.flow_gate import GateDecision, evaluate_flow_gate
from odte_spy_ibkr.strategy.macro_gate import evaluate_macro_gate

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SpreadIntent:
    spread: VerticalSpreadSelection
    side: CreditSide
    spot: float
    net_call_risk: float
    contracts: int
    reason: str
    as_of: datetime | None = None


@dataclass(frozen=True)
class OpenPosition:
    spread: VerticalSpreadSelection
    side: CreditSide
    contracts: int
    entry_credit: float
    opened_at: datetime


def propose_entry(
    settings: Settings,
    ctx: MarketContext,
    candidates: Sequence[OptionCandidate],
    *,
    contracts: int = 1,
    now: datetime | None = None,
) -> SpreadIntent | None:
    """Entry decision shared by live and backtest.

    `now` is the decision time: wall clock when omitted (live), the simulated minute in a
    backtest. It only feeds the context-staleness check.
    """
    macro = evaluate_macro_gate(settings, ctx)
    if macro.blocked:
        return None
    flow = evaluate_flow_gate(settings, ctx, now=now)
    if flow.decision == GateDecision.BLOCK:
        return None

    plan = build_entry_plan(settings, ctx, candidates, contracts=contracts, now=now)
    if plan is None:
        return None

    net_risk = net_call_risk_notional(plan.spread, contracts=contracts, spot=plan.spot)
    if not net_call_risk_within_limit(
        plan.spread,
        max_net_call_risk=settings.max_net_call_risk,
        contracts=contracts,
    ):
        return None

    size = max(1, int(contracts * macro.size_scale))
    return SpreadIntent(
        spread=plan.spread,
        side=plan.side,
        spot=plan.spot or ctx.spot or 0.0,
        net_call_risk=net_risk,
        contracts=size,
        reason=f"{flow.reason};{macro.reason}",
        as_of=ctx.as_of,
    )


def manage_open(
    settings: Settings,
    position: OpenPosition,
    ctx: MarketContext,
    *,
    now: datetime | None = None,
    spread_mark: float | None = None,
) -> str:
    """Return action: hold | flatten | stop.

    `spread_mark` is the current per-share cost to buy the spread back; without it the
    optional stop cannot trigger.
    """
    now = now or datetime.now(tz=ET)
    if now.astimezone(ET).time() >= settings.flatten_time:
        return "flatten"
    if (
        spread_mark is not None
        and settings.stop_loss_credit_multiple > 0
        and position.entry_credit > 0
        and spread_mark >= settings.stop_loss_credit_multiple * position.entry_credit
    ):
        return "stop"
    if ctx.spot is None:
        return "hold"
    if position.side == CreditSide.PUT and ctx.macro_event_blackout:
        return "flatten"
    if position.side == CreditSide.CALL and ctx.net_call_bias > settings.strong_bullish_skew:
        return "flatten"
    return "hold"
