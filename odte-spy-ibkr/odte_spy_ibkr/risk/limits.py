from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


class RiskDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class RiskState:
    realized_pnl: float = 0.0
    open_contracts: int = 0


@dataclass(frozen=True)
class RiskLimits:
    max_contracts: int
    max_daily_loss: float
    entry_cutoff: time
    flatten_time: time
    min_minutes_after_open: int = 30


def is_rth(now: datetime | None = None) -> bool:
    now = now or datetime.now(tz=ET)
    if now.weekday() >= 5:
        return False
    t = now.timetz().replace(tzinfo=None)
    return RTH_OPEN <= t < RTH_CLOSE


def minutes_since_open(now: datetime | None = None) -> float:
    now = now or datetime.now(tz=ET)
    open_dt = now.replace(hour=RTH_OPEN.hour, minute=RTH_OPEN.minute, second=0, microsecond=0)
    return max(0.0, (now - open_dt).total_seconds() / 60.0)


def should_flatten(limits: RiskLimits, now: datetime | None = None) -> bool:
    now = now or datetime.now(tz=ET)
    t = now.timetz().replace(tzinfo=None)
    return t >= limits.flatten_time


def check_new_entry(
    limits: RiskLimits,
    state: RiskState,
    *,
    now: datetime | None = None,
) -> tuple[RiskDecision, str]:
    now = now or datetime.now(tz=ET)
    if not is_rth(now):
        return RiskDecision.BLOCK, "outside_rth"
    if minutes_since_open(now) < limits.min_minutes_after_open:
        return RiskDecision.BLOCK, "liquidity_window"
    t = now.timetz().replace(tzinfo=None)
    if t >= limits.entry_cutoff:
        return RiskDecision.BLOCK, "entry_cutoff"
    if state.open_contracts >= limits.max_contracts:
        return RiskDecision.BLOCK, "max_contracts"
    if state.realized_pnl <= -abs(limits.max_daily_loss):
        return RiskDecision.BLOCK, "max_daily_loss"
    return RiskDecision.ALLOW, "ok"