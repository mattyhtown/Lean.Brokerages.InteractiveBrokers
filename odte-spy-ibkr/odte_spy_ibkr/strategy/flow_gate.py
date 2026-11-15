from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from odte_spy_ibkr.config import CreditSide, Settings, UWMode
from odte_spy_ibkr.providers.base import FlowBias, MarketContext


class GateDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    LOG_ONLY = "log_only"


@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    reason: str
    bias: FlowBias
    strength: float


def resolved_credit_side(config_side: CreditSide, ctx: MarketContext) -> CreditSide:
    if config_side != CreditSide.AUTO:
        return config_side
    if ctx.flow_bias == FlowBias.BEARISH:
        return CreditSide.CALL
    return CreditSide.PUT


def flow_allows_side(side: CreditSide, bias: FlowBias) -> bool:
    if side == CreditSide.PUT:
        return bias in {FlowBias.BULLISH, FlowBias.NEUTRAL}
    if side == CreditSide.CALL:
        return bias in {FlowBias.BEARISH, FlowBias.NEUTRAL}
    return True


def evaluate_flow_gate(
    settings: Settings,
    ctx: MarketContext,
    *,
    now: datetime | None = None,
) -> GateResult:
    now = now or datetime.now(tz=timezone.utc)
    bias, strength = ctx.effective_bias_strength()
    side = resolved_credit_side(settings.credit_side, ctx)

    if settings.uw_mode == UWMode.ALERTS_ONLY:
        return GateResult(GateDecision.LOG_ONLY, "alerts_only", bias, strength)

    if ctx.macro_risk_off and settings.uw_required:
        return GateResult(GateDecision.BLOCK, "macro_risk_off", bias, strength)

    stale = False
    if ctx.as_of:
        stale = (now - ctx.as_of) > timedelta(seconds=settings.uw_stale_block_sec)

    if strength < settings.uw_min_strength:
        if settings.uw_required or settings.uw_flow_required:
            return GateResult(GateDecision.BLOCK, "low_flow_strength", bias, strength)
        return GateResult(GateDecision.LOG_ONLY, "weak_flow", bias, strength)

    if not flow_allows_side(side, bias):
        if settings.uw_required or settings.uw_flow_required:
            return GateResult(GateDecision.BLOCK, "bias_mismatch", bias, strength)
        return GateResult(GateDecision.LOG_ONLY, "bias_mismatch_soft", bias, strength)

    if stale and settings.uw_required:
        return GateResult(GateDecision.BLOCK, "stale_market_context", bias, strength)

    return GateResult(GateDecision.ALLOW, "aligned", bias, strength)