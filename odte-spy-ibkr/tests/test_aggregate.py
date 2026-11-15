from datetime import datetime, timezone

from odte_spy_ibkr.providers.aggregate import merge_deltas
from odte_spy_ibkr.providers.base import FlowBias, MarketContext, MarketContextDelta


def test_merge_spot_and_flow():
    seed = MarketContext(underlying="SPY")
    deltas = [
        MarketContextDelta(provider_id="ibkr", spot=500.0, spot_weight=1.0),
        MarketContextDelta(provider_id="alpaca", spot=501.0, spot_weight=0.5),
        MarketContextDelta(
            provider_id="unusual_whales",
            flow_bias=FlowBias.BULLISH,
            flow_strength=0.6,
            flow_weight=1.0,
            as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    ctx = merge_deltas(seed, deltas, {"ibkr": {"spot": 1.0}, "alpaca": {"spot": 0.5}, "unusual_whales": {"flow": 1.0}})
    assert ctx.spot is not None
    assert ctx.flow_bias == FlowBias.BULLISH
    assert ctx.flow_strength > 0