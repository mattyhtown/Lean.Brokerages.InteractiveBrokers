"""Black-Scholes helpers (zero rate, no dividends) for intraday 0DTE marks.

Used where a data source has prices but no greeks (e.g. Alpaca historical option bars).
"""

from __future__ import annotations

import math

MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def intrinsic_value(spot: float, strike: float, right: str) -> float:
    return max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)


def _d1(spot: float, strike: float, t_years: float, vol: float) -> float:
    sd = vol * math.sqrt(t_years)
    return (math.log(spot / strike) + 0.5 * sd * sd) / sd


def bs_price(spot: float, strike: float, t_years: float, vol: float, right: str) -> float:
    if t_years <= 0 or vol <= 0:
        return intrinsic_value(spot, strike, right)
    d1 = _d1(spot, strike, t_years, vol)
    d2 = d1 - vol * math.sqrt(t_years)
    call = spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return call if right == "C" else call - spot + strike


def bs_delta(spot: float, strike: float, t_years: float, vol: float, right: str) -> float:
    if t_years <= 0 or vol <= 0:
        if right == "C":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    call_delta = _norm_cdf(_d1(spot, strike, t_years, vol))
    return call_delta if right == "C" else call_delta - 1.0


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    t_years: float,
    right: str,
    *,
    lo: float = 1e-4,
    hi: float = 10.0,
) -> float | None:
    """Bisection solve; None when the price carries no time value or exceeds the model bound."""
    if t_years <= 0 or price <= intrinsic_value(spot, strike, right) + 1e-9:
        return None
    if price >= bs_price(spot, strike, t_years, hi, right):
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs_price(spot, strike, t_years, mid, right) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)
