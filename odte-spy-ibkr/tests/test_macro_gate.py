from datetime import datetime, timezone

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.strategy.macro_gate import MacroDecision, evaluate_macro_gate


def test_blackout_blocks():
    settings = Settings(MACRO_ENABLED=True)
    ctx = MarketContext(macro_event_blackout=True)
    result = evaluate_macro_gate(settings, ctx)
    assert result.decision == MacroDecision.BLOCK