import json
from pathlib import Path

from odte_spy_ibkr.unusual_whales.signals import FlowBias, aggregate_flow_signals, signals_from_payload

FIXTURE = Path(__file__).parent / "fixtures" / "uw_flow_recent.json"


def test_signals_from_fixture():
    payload = json.loads(FIXTURE.read_text())
    signals = signals_from_payload(payload)
    assert len(signals) >= 2
    agg = aggregate_flow_signals(signals)
    assert agg.bias in FlowBias
    assert 0.0 <= agg.strength <= 1.0