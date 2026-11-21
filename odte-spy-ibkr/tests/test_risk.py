from datetime import datetime, time
from zoneinfo import ZoneInfo

from odte_spy_ibkr.risk import (
    ET,
    RiskDecision,
    RiskLimits,
    RiskState,
    check_new_entry,
    minutes_since_open,
    should_flatten,
)

def test_check_new_entry_blocks_after_cutoff():
    limits = RiskLimits(1, 500, entry_cutoff=time(9, 35), flatten_time=time(15, 45))
    state = RiskState()
    now = datetime(2026, 11, 17, 10, 0, tzinfo=ET)  # Monday
    decision, _ = check_new_entry(limits, state, now=now)
    assert decision == RiskDecision.BLOCK


def test_check_new_entry_allows_midday():
    limits = RiskLimits(1, 500, entry_cutoff=time(15, 0), flatten_time=time(15, 45), min_minutes_after_open=0)
    state = RiskState()
    now = datetime(2026, 11, 17, 11, 0, tzinfo=ET)
    decision, reason = check_new_entry(limits, state, now=now)
    assert decision == RiskDecision.ALLOW
    assert reason == "ok"


def test_max_daily_loss():
    limits = RiskLimits(2, 100, time(15, 30), time(15, 45), min_minutes_after_open=0)
    state = RiskState(realized_pnl=-150)
    now = datetime(2026, 11, 17, 11, 0, tzinfo=ET)
    decision, reason = check_new_entry(limits, state, now=now)
    assert decision == RiskDecision.BLOCK
    assert reason == "max_daily_loss"


def test_should_flatten_after_time():
    limits = RiskLimits(1, 500, time(15, 0), time(15, 45), min_minutes_after_open=0)
    now = datetime(2026, 11, 17, 15, 50, tzinfo=ET)
    assert should_flatten(limits, now)


def test_minutes_since_open_at_open():
    now = datetime(2026, 11, 17, 9, 30, tzinfo=ET)
    assert minutes_since_open(now) == 0.0