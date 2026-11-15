from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odte_spy_ibkr.config import CreditSide, MacroFlowConflict, Settings
from odte_spy_ibkr.providers.base import FlowBias, MarketContext
from odte_spy_ibkr.strategy.flow_gate import resolved_credit_side


class MacroDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    DOWNSIZE = "downsize"


@dataclass(frozen=True)
class MacroGateResult:
    decision: MacroDecision
    reason: str
    size_scale: float = 1.0

    @property
    def blocked(self) -> bool:
        return self.decision == MacroDecision.BLOCK


def evaluate_macro_gate(settings: Settings, ctx: MarketContext) -> MacroGateResult:
    if not settings.macro_enabled:
        return MacroGateResult(MacroDecision.ALLOW, "macro_disabled", 1.0)

    if ctx.macro_event_blackout:
        return MacroGateResult(MacroDecision.BLOCK, "tier1_blackout", 0.0)

    if abs(ctx.macro_headline_score) >= settings.macro_headline_intensity_block:
        if settings.macro_flow_conflict == MacroFlowConflict.BLOCK:
            return MacroGateResult(MacroDecision.BLOCK, "headline_shock", 0.0)
        return MacroGateResult(
            MacroDecision.DOWNSIZE,
            "headline_shock",
            settings.macro_size_scale_under_stress,
        )

    if ctx.macro_risk_off:
        return MacroGateResult(
            MacroDecision.DOWNSIZE,
            "macro_risk_off",
            settings.macro_size_scale_under_stress,
        )

    side = resolved_credit_side(settings.credit_side, ctx)
    if ctx.macro_headline_score < -0.5 and side == CreditSide.PUT:
        if settings.macro_flow_conflict == MacroFlowConflict.BLOCK and ctx.flow_bias != FlowBias.BEARISH:
            return MacroGateResult(MacroDecision.BLOCK, "bearish_macro_vs_put_credit", 0.0)
        if settings.macro_flow_conflict == MacroFlowConflict.DOWNSIZE:
            return MacroGateResult(MacroDecision.DOWNSIZE, "bearish_macro_vs_put_credit", settings.macro_size_scale_under_stress)

    if (
        ctx.macro_headline_score < -0.5
        and ctx.flow_bias == FlowBias.BULLISH
        and settings.macro_flow_conflict == MacroFlowConflict.BLOCK
    ):
        return MacroGateResult(MacroDecision.BLOCK, "macro_flow_conflict", 0.0)

    return MacroGateResult(MacroDecision.ALLOW, "ok", 1.0)