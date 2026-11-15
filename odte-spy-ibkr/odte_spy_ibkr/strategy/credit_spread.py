from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from odte_spy_ibkr.chain import (
    OptionCandidate,
    VerticalSpreadSelection,
    build_vertical_spread,
    net_call_risk_within_limit,
    select_by_delta_band,
    select_by_otm_distance,
)
from odte_spy_ibkr.config import CreditSide, Settings, UWMode
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.strategy.flow_gate import (
    GateDecision,
    GateResult,
    evaluate_flow_gate,
    resolved_credit_side,
)


@dataclass(frozen=True)
class EntryPlan:
    spread: VerticalSpreadSelection
    side: CreditSide
    gate: GateResult
    spot: float | None


def choose_short_leg(
    settings: Settings,
    candidates: Sequence[OptionCandidate],
    *,
    right: str,
    spot: float,
) -> OptionCandidate | None:
    if settings.otm_distance is not None:
        return select_by_otm_distance(
            candidates,
            right=right,  # type: ignore[arg-type]
            spot=spot,
            distance=settings.otm_distance,
        )
    return select_by_delta_band(
        candidates,
        right=right,  # type: ignore[arg-type]
        delta_min=settings.short_delta_min,
        delta_max=settings.short_delta_max,
    )


def build_entry_plan(
    settings: Settings,
    ctx: MarketContext,
    candidates: Sequence[OptionCandidate],
    *,
    contracts: int = 1,
    now: datetime | None = None,
) -> EntryPlan | None:
    gate = evaluate_flow_gate(settings, ctx, now=now)
    if gate.decision == GateDecision.BLOCK:
        return None

    spot = ctx.spot
    if spot is None:
        return None

    side = resolved_credit_side(settings.credit_side, ctx)
    # Avoid call credit when chain skew is strongly bullish (net call pressure).
    if (
        side == CreditSide.CALL
        and ctx.net_call_bias >= settings.strong_bullish_skew
        and settings.credit_side == CreditSide.AUTO
    ):
        side = CreditSide.PUT

    right = "P" if side == CreditSide.PUT else "C"
    short = choose_short_leg(settings, candidates, right=right, spot=spot)
    if short is None:
        return None

    spread = build_vertical_spread(short, candidates, width=settings.spread_width)
    if spread is None:
        return None
    if spread.estimated_credit is not None and spread.estimated_credit < settings.min_credit:
        return None
    if not net_call_risk_within_limit(
        spread,
        max_net_call_risk=settings.max_net_call_risk,
        contracts=contracts,
    ):
        return None

    if gate.decision == GateDecision.LOG_ONLY and settings.uw_mode == UWMode.GATE:
        return None

    return EntryPlan(spread=spread, side=side, gate=gate, spot=spot)