from __future__ import annotations

from typing import Sequence

from odte_spy_ibkr.chain import OptionCandidate
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.strategy.credit_spread import build_entry_plan
from odte_spy_ibkr.strategy.flow_gate import GateDecision, evaluate_flow_gate
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
    plan = build_entry_plan(settings, ctx, candidates)
    if plan is None:
        from odte_spy_ibkr.chain import build_vertical_spread, select_by_delta_band
        from odte_spy_ibkr.strategy.flow_gate import resolved_credit_side
        from odte_spy_ibkr.config import CreditSide

        side = resolved_credit_side(settings.credit_side, ctx)
        right = "P" if side == CreditSide.PUT else "C"
        spot = ctx.spot or 0.0
        short = select_by_delta_band(
            candidates,
            right=right,  # type: ignore[arg-type]
            delta_min=settings.short_delta_min,
            delta_max=settings.short_delta_max,
        )
        if short is None:
            return "no_short_leg_in_delta_band"
        spread = build_vertical_spread(short, candidates, width=settings.spread_width)
        if spread is None:
            return "missing_long_leg_for_spread_width"
        if spread.estimated_credit is None or spread.estimated_credit < settings.min_credit:
            return f"min_credit:{spread.estimated_credit}"
        return "no_spread:unknown"
    return "ok"