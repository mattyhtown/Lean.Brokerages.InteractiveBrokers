from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research.alpaca_ingest import RESEARCH_ROOT, load_minute_bars, save_json
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.features import SIGNAL_TIME, _price_at

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _headers(settings: Settings) -> dict[str, str]:
    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_API_SECRET are required for Alpaca option-bar chain ingest."
        )
    return {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }


def occ_symbol(underlying: str, day: date, strike: float, right: str) -> str:
    strike_m = int(round(strike * 1000))
    return f"{underlying}{day.strftime('%y%m%d')}{right}{strike_m:08d}"


def strikes_around(spot: float, *, radius: int = 15) -> list[int]:
    center = int(round(spot))
    return list(range(center - radius, center + radius + 1))


def fetch_option_bars_batch(
    settings: Settings,
    symbols: list[str],
    *,
    day: date,
    window_start: dt_time,
    window_end: dt_time,
) -> dict[str, list[dict[str, Any]]]:
    base = settings.alpaca_data_url.rstrip("/")
    url = f"{base}/v1beta1/options/bars"
    start = datetime.combine(day, window_start, tzinfo=ET).isoformat()
    end = datetime.combine(day, window_end, tzinfo=ET).isoformat()
    out: dict[str, list[dict[str, Any]]] = {}
    chunk = 50
    with httpx.Client(timeout=60.0) as client:
        for i in range(0, len(symbols), chunk):
            batch = symbols[i : i + chunk]
            params = {
                "symbols": ",".join(batch),
                "timeframe": "1Min",
                "start": start,
                "end": end,
                "limit": 500,
            }
            resp = client.get(url, headers=_headers(settings), params=params)
            if resp.status_code >= 400:
                logger.warning("Alpaca option bars %s: %s", resp.status_code, resp.text[:200])
                continue
            bars = (resp.json().get("bars") or {})
            for sym, rows in bars.items():
                out[sym] = rows
    return out


def _bar_time_et(bar: dict[str, Any]) -> datetime:
    raw = str(bar["t"])
    if raw.endswith("Z"):
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
    return datetime.fromisoformat(raw)


def _pick_bar_in_window(series: list[dict[str, Any]], start: dt_time, end: dt_time) -> dict[str, Any] | None:
    picks = []
    for bar in series:
        ts = _bar_time_et(bar)
        if start <= ts.time() <= end:
            picks.append(bar)
    return picks[-1] if picks else None


def bars_to_chain_rows(
    symbol_map: dict[str, tuple[float, str]],
    bars: dict[str, list[dict[str, Any]]],
    *,
    day: date,
    window_start: dt_time | None = None,
    window_end: dt_time | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym, (strike, right) in symbol_map.items():
        series = bars.get(sym) or []
        if not series:
            continue
        if window_start is not None and window_end is not None:
            pick = _pick_bar_in_window(series, window_start, window_end)
        else:
            pick = series[-1]
        if not pick:
            continue
        mid = float(pick.get("c") or pick.get("vw") or 0)
        if mid <= 0:
            continue
        rows.append(
            {
                "symbol": sym,
                "strike": strike,
                "right": right,
                "bid": mid,
                "ask": mid,
                "delta": None,
                "volume": float(pick.get("v") or 0),
                "open_interest": 0.0,
            }
        )
    return rows


def ingest_chains_from_option_bars(
    settings: Settings,
    *,
    start: date,
    end: date,
    mcp_fetcher: Any | None = None,
) -> dict[str, Any]:
    """
    Build per-day 0DTE chains from Alpaca historical option bars.
    `mcp_fetcher(symbols, day, start_time, end_time) -> dict` optional for agent MCP backfill.
    """
    underlying = settings.underlying
    stats = {"days_chain": 0, "days_empty": 0, "days_no_minute": 0, "option_earliest_ok": None}
    for day in iter_trading_days(start, end):
        minute = load_minute_bars(day, RESEARCH_ROOT)
        if not minute:
            stats["days_no_minute"] += 1
            continue
        spot = _price_at(minute, SIGNAL_TIME) or _price_at(minute, dt_time(9, 35))
        if not spot or spot <= 0:
            stats["days_empty"] += 1
            continue
        symbol_map: dict[str, tuple[float, str]] = {}
        for strike in strikes_around(spot):
            for right in ("P", "C"):
                sym = occ_symbol(underlying, day, float(strike), right)
                symbol_map[sym] = (float(strike), right)
        symbols = list(symbol_map.keys())
        if mcp_fetcher is not None:
            signal_bars = mcp_fetcher(symbols, day, SIGNAL_TIME, dt_time(11, 5))
            close_bars = mcp_fetcher(symbols, day, dt_time(15, 55), dt_time(16, 5))
        else:
            signal_bars = fetch_option_bars_batch(settings, symbols, day=day, window_start=SIGNAL_TIME, window_end=dt_time(11, 5))
            time.sleep(0.2)
            close_bars = fetch_option_bars_batch(
                settings, symbols, day=day, window_start=dt_time(15, 55), window_end=dt_time(16, 5)
            )
        signal_rows = bars_to_chain_rows(symbol_map, signal_bars, day=day)
        close_rows = bars_to_chain_rows(symbol_map, close_bars, day=day)
        if not signal_rows:
            stats["days_empty"] += 1
            continue
        save_chain_snapshot(signal_rows, day, snapshot="signal", source="alpaca_option_bars")
        if close_rows:
            save_chain_snapshot(close_rows, day, snapshot="close", source="alpaca_option_bars")
        stats["days_chain"] += 1
    manifest = RESEARCH_ROOT / "option_chain_ingest.json"
    save_json(manifest, stats)
    return stats