from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from odte_spy_ibkr.research.features import RTH_CLOSE, SIGNAL_TIME

ET = ZoneInfo("America/New_York")
RISK_FREE = 0.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1(spot: float, strike: float, t: float, vol: float, r: float = RISK_FREE) -> float:
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return float("nan")
    return (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))


def black_scholes_price(
    *,
    spot: float,
    strike: float,
    t: float,
    vol: float,
    right: Literal["P", "C"],
    r: float = RISK_FREE,
) -> float | None:
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return None
    d1 = _d1(spot, strike, t, vol, r)
    if not math.isfinite(d1):
        return None
    d2 = d1 - vol * math.sqrt(t)
    disc = strike * math.exp(-r * t)
    if right == "C":
        return spot * _norm_cdf(d1) - disc * _norm_cdf(d2)
    return disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def black_scholes_delta(
    *,
    spot: float,
    strike: float,
    t: float,
    vol: float,
    right: Literal["P", "C"],
    r: float = RISK_FREE,
) -> float | None:
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return None
    d1 = _d1(spot, strike, t, vol, r)
    if not math.isfinite(d1):
        return None
    if right == "C":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0


def years_to_expiry(as_of: datetime, expiry_close: datetime) -> float:
    seconds = (expiry_close - as_of).total_seconds()
    if seconds <= 0:
        return 0.0
    return seconds / (365.25 * 24.0 * 3600.0)


def session_expiry_close(day: date) -> datetime:
    return datetime.combine(day, RTH_CLOSE, tzinfo=ET)


def default_signal_as_of(day: date) -> datetime:
    return datetime.combine(day, SIGNAL_TIME, tzinfo=ET)


def implied_volatility(
    *,
    price: float,
    spot: float,
    strike: float,
    t: float,
    right: Literal["P", "C"],
    r: float = RISK_FREE,
) -> float | None:
    """Bisection IV solve; returns None if price invalid or no root."""
    if price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return None
    intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    if price + 1e-6 < intrinsic:
        return None
    lo, hi = 1e-4, 5.0
    p_lo = black_scholes_price(spot=spot, strike=strike, t=t, vol=lo, right=right, r=r)
    p_hi = black_scholes_price(spot=spot, strike=strike, t=t, vol=hi, right=right, r=r)
    if p_lo is None or p_hi is None:
        return None
    if price < p_lo - 1e-8 or price > p_hi + 1e-8:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2.0
        p_mid = black_scholes_price(spot=spot, strike=strike, t=t, vol=mid, right=right, r=r)
        if p_mid is None:
            return None
        if abs(p_mid - price) < 1e-5:
            return mid
        if p_mid < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def delta_from_mid(
    *,
    mid: float,
    spot: float,
    strike: float,
    right: Literal["P", "C"],
    as_of: datetime,
    day: date,
) -> float | None:
    expiry = session_expiry_close(day)
    t = years_to_expiry(as_of.astimezone(ET), expiry)
    if t <= 0:
        return None
    vol = implied_volatility(price=mid, spot=spot, strike=strike, t=t, right=right)
    if vol is None:
        return None
    return black_scholes_delta(spot=spot, strike=strike, t=t, vol=vol, right=right)