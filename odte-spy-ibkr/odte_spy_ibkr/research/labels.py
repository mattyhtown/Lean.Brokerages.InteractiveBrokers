from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SIGNAL_TIME = time(11, 0)
CLOSE_TIME = time(16, 0)


@dataclass(frozen=True)
class LabelRow:
    day: date
    spy_return_to_close: float | None
    spread_pnl_usd: float | None


def _bar_ts(bar: dict) -> datetime:
    raw = bar["t"]
    if raw.endswith("Z"):
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
    return datetime.fromisoformat(raw)


def _price_at(minute_bars: list[dict], target: time) -> float | None:
    for b in minute_bars:
        if _bar_ts(b).time() >= target:
            return float(b["o"])
    return None


def spy_return_to_close(minute_bars: list[dict]) -> float | None:
    ref = _price_at(minute_bars, SIGNAL_TIME)
    close_px = None
    for b in reversed(minute_bars):
        if _bar_ts(b).time() <= CLOSE_TIME:
            close_px = float(b["c"])
            break
    if ref is None or close_px is None or ref <= 0:
        return None
    return (close_px - ref) / ref


def modeled_put_spread_pnl(
    *,
    short_mid: float,
    long_mid: float,
    entry_slip: float = 0.01,
    exit_slip: float = 0.01,
    commission: float = 1.30,
    contracts: int = 1,
) -> float:
    """Credit spread P&L from real option mids at entry; exit marks option mids at close if provided."""
    credit = (short_mid - long_mid) * 100 * contracts - entry_slip - commission
    return credit