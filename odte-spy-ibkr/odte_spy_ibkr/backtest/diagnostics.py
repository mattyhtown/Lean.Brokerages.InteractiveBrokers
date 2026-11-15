from __future__ import annotations

from typing import Sequence

from odte_spy_ibkr.chain import OptionCandidate, build_vertical_spread
from odte_spy_ibkr.config import CreditSide, Settings
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.strategy.credit_spread import build_entry_plan, choose_short_leg
from odte_spy_ibkr.strategy.flow_gate import GateDecision, evaluate_flow_gate, resolved_credit_side
from odte_spy_ibkr.strategy.macro_gate import evaluate_macro_gate


def explain_no_entry(
    settings: Settings,
    ctx: MarketContext,
    candidates: Sequence[OptionCandidate],
) -> str:
    if ctx is None:
        return "missing_market_context"
    macro = evaluate_macro_gate(settings, ctx)
    if macro.blocked:
        return f"macro_gate:{macro.reason}"
    now = ctx.as_of
    flow = evaluate_flow_gate(settings, ctx, now=now)
    if flow.decision == GateDecision.BLOCK:
        return f"flow_gate:{flow.reason}"
    plan = build_entry_plan(settings, ctx, candidates, now=now)
    if plan is None:
        if not candidates:
            return "no_chain"
        side = resolved_credit_side(settings.credit_side, ctx)
        right = "P" if side == CreditSide.PUT else "C"
        short = choose_short_leg(settings, candidates, right=right, spot=ctx.spot or 0.0)
        if short is None:
            return "no_short_leg_in_delta_band"
        spread = build_vertical_spread(short, candidates, width=settings.spread_width)
        if spread is None:
            return "missing_long_leg_for_spread_width"
        if spread.estimated_credit is None or spread.estimated_credit < settings.min_credit:
            return f"min_credit:{spread.estimated_credit}"
        return "no_spread:unknown"
    return "ok"