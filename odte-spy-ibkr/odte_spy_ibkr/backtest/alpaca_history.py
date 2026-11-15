"""Historical 1-minute bars from Alpaca Market Data for one 0DTE session.

Option history is trade bars only (no NBBO quotes, no greeks) and starts around
February 2024; `session_data` derives marks and deltas from these bars.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx

from odte_spy_ibkr.backtest.calendar import session_close, session_open
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.alpaca import alpaca_paged_get

MAX_SYMBOLS_PER_REQUEST = 100
PAGE_LIMIT = 10_000


def occ_symbol(ticker: str, day: date, right: str, strike: float) -> str:
    return f"{ticker.upper()}{day.strftime('%y%m%d')}{right}{int(round(strike * 1000)):08d}"


def parse_occ_symbol(symbol: str) -> tuple[str, float]:
    """(right, strike) from an OCC symbol such as SPY240603P00450000."""
    return symbol[-9], int(symbol[-8:]) / 1000.0


def _session_window(day: date) -> dict[str, str]:
    def fmt(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {"start": fmt(session_open(day)), "end": fmt(session_close(day))}


def _bar_row(bar: dict[str, Any]) -> dict[str, Any]:
    return {
        "t": bar["t"],
        "o": float(bar["o"]),
        "h": float(bar["h"]),
        "l": float(bar["l"]),
        "c": float(bar["c"]),
        "v": float(bar.get("v") or 0),
    }


def fetch_stock_minute_bars(
    settings: Settings,
    ticker: str,
    day: date,
    *,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """GET /v2/stocks/{symbol}/bars for the regular session of `day`."""
    params = {
        "timeframe": "1Min",
        "limit": PAGE_LIMIT,
        "adjustment": "raw",
        "feed": settings.backtest_stock_feed,
        "sort": "asc",
        **_session_window(day),
    }
    rows: list[dict[str, Any]] = []
    for page in alpaca_paged_get(settings, f"/v2/stocks/{ticker}/bars", params, client=client):
        rows.extend(_bar_row(bar) for bar in page.get("bars") or [])
    return rows


def odte_symbols(ticker: str, day: date, center: float, strike_range: int) -> list[str]:
    """$1 strikes within `strike_range` of `center` (the session open), both rights."""
    mid = int(round(center))
    return [
        occ_symbol(ticker, day, right, strike)
        for strike in range(mid - strike_range, mid + strike_range + 1)
        for right in ("P", "C")
    ]


def fetch_option_minute_bars(
    settings: Settings,
    symbols: list[str],
    day: date,
    *,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """GET /v1beta1/options/bars for `symbols`; one row per (symbol, minute) with a trade."""
    rows: list[dict[str, Any]] = []
    for i in range(0, len(symbols), MAX_SYMBOLS_PER_REQUEST):
        params = {
            "symbols": ",".join(symbols[i : i + MAX_SYMBOLS_PER_REQUEST]),
            "timeframe": "1Min",
            "limit": PAGE_LIMIT,
            "sort": "asc",
            **_session_window(day),
        }
        for page in alpaca_paged_get(settings, "/v1beta1/options/bars", params, client=client):
            for symbol, bars in (page.get("bars") or {}).items():
                right, strike = parse_occ_symbol(symbol)
                for bar in bars:
                    rows.append({"symbol": symbol, "strike": strike, "right": right, **_bar_row(bar)})
    return rows
