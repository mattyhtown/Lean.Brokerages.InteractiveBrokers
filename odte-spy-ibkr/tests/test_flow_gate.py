from datetime import datetime, timezone

from odte_spy_ibkr.config import CreditSide, Settings, UWMode
from odte_spy_ibkr.providers.base import FlowBias, MarketContext
from odte_spy_ibkr.strategy.flow_gate import GateDecision, evaluate_flow_gate


def test_alerts_only_never_blocks():
    settings = Settings(UW_MODE=UWMode.ALERTS_ONLY, UW_MIN_STRENGTH=0.9)
    ctx = MarketContext(flow_bias=FlowBias.BEARISH, flow_strength=0.1, as_of=datetime.now(tz=timezone.utc))
    result = evaluate_flow_gate(settings, ctx)
    assert result.decision == GateDecision.LOG_ONLY


def test_gate_blocks_bias_mismatch_when_required():
    settings = Settings(
        UW_MODE=UWMode.GATE,
        UW_REQUIRED=True,
        UW_MIN_STRENGTH=0.2,
        CREDIT_SIDE=CreditSide.PUT,
    )
    ctx = MarketContext(flow_bias=FlowBias.BEARISH, flow_strength=0.8, as_of=datetime.now(tz=timezone.utc))
    result = evaluate_flow_gate(settings, ctx)
    assert result.decision == GateDecision.BLOCK
    assert result.reason == "bias_mismatch"


def test_macro_risk_off_blocks_when_required():
    settings = Settings(UW_MODE=UWMode.GATE, UW_REQUIRED=True, UW_MIN_STRENGTH=0.1)
    ctx = MarketContext(
        flow_bias=FlowBias.BULLISH,
        flow_strength=0.9,
        macro_risk_off=True,
        as_of=datetime.now(tz=timezone.utc),
    )
    result = evaluate_flow_gate(settings, ctx)
    assert result.decision == GateDecision.BLOCK
    assert result.reason == "macro_risk_off"