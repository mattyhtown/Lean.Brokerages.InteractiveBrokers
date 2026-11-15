from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Sequence

OptionRight = Literal["P", "C"]


@dataclass(frozen=True)
class OptionCandidate:
    symbol: str
    strike: float
    right: OptionRight
    expiry: date
    delta: float | None = None
    bid: float | None = None
    ask: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0 and self.ask <= 0:
            return None
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class VerticalSpreadSelection:
    short_leg: OptionCandidate
    long_leg: OptionCandidate
    width: float
    estimated_credit: float | None


def today_expiry(trading_day: date | None = None) -> date:
    return trading_day or date.today()


def filter_expiry(candidates: Sequence[OptionCandidate], expiry: date) -> list[OptionCandidate]:
    return [c for c in candidates if c.expiry == expiry]


def select_by_delta_band(
    candidates: Sequence[OptionCandidate],
    *,
    right: OptionRight,
    delta_min: float,
    delta_max: float,
) -> OptionCandidate | None:
    pool = [
        c
        for c in candidates
        if c.right == right and c.delta is not None and delta_min <= abs(c.delta) <= delta_max
    ]
    if not pool:
        return None
    return min(pool, key=lambda c: abs(abs(c.delta or 0) - (delta_min + delta_max) / 2))


def select_by_otm_distance(
    candidates: Sequence[OptionCandidate],
    *,
    right: OptionRight,
    spot: float,
    distance: float,
) -> OptionCandidate | None:
    if right == "P":
        target = spot - distance
        pool = [c for c in candidates if c.right == "P" and c.strike <= target]
        if not pool:
            return None
        return max(pool, key=lambda c: c.strike)
    target = spot + distance
    pool = [c for c in candidates if c.right == "C" and c.strike >= target]
    if not pool:
        return None
    return min(pool, key=lambda c: c.strike)


def build_vertical_spread(
    short_leg: OptionCandidate,
    candidates: Sequence[OptionCandidate],
    *,
    width: float,
) -> VerticalSpreadSelection | None:
    if short_leg.right == "P":
        long_strike = short_leg.strike - width
        long_legs = [
            c for c in candidates if c.right == "P" and c.expiry == short_leg.expiry and c.strike == long_strike
        ]
    else:
        long_strike = short_leg.strike + width
        long_legs = [
            c for c in candidates if c.right == "C" and c.expiry == short_leg.expiry and c.strike == long_strike
        ]
    if not long_legs:
        return None
    long_leg = long_legs[0]
    credit: float | None = None
    if short_leg.bid is not None and long_leg.ask is not None:
        credit = short_leg.bid - long_leg.ask
    return VerticalSpreadSelection(
        short_leg=short_leg,
        long_leg=long_leg,
        width=width,
        estimated_credit=credit,
    )


def chain_snapshot_timestamp() -> datetime:
    return datetime.now()