from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.backtest.calendar import iter_trading_days

logger = logging.getLogger(__name__)

RESEARCH_ROOT = Path("data/research/alpaca")


def _headers(settings: Settings) -> dict[str, str]:
    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_API_SECRET are required for real-data ingest "
            "(set in .env or pass via Cursor secrets). MCP trading tools can validate access "
            "but this CLI uses the Data API directly."
        )
    return {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }


def fetch_stock_bars(
    settings: Settings,
    *,
    symbols: str = "SPY",
    timeframe: str = "1Min",
    start: date,
    end: date,
    feed: str = "sip",
) -> dict[str, list[dict[str, Any]]]:
    url = f"{settings.alpaca_data_url.rstrip('/')}/v2/stocks/bars"
    params = {
        "symbols": symbols,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "limit": 10000,
        "feed": feed,
        "adjustment": "raw",
    }
    out: dict[str, list[dict[str, Any]]] = {symbols: []}
    page: str | None = None
    with httpx.Client(timeout=60.0) as client:
        while True:
            if page:
                params["page_token"] = page
            resp = client.get(url, headers=_headers(settings), params=params)
            resp.raise_for_status()
            payload = resp.json()
            bars = payload.get("bars") or {}
            for sym, rows in bars.items():
                out.setdefault(sym, []).extend(rows)
            page = payload.get("next_page_token")
            if not page:
                break
    return out


def fetch_daily_bars(settings: Settings, *, start: date, end: date) -> list[dict[str, Any]]:
    return fetch_stock_bars(settings, timeframe="1Day", start=start, end=end).get("SPY", [])


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ingest_range(settings: Settings, *, start: date, end: date) -> dict[str, str]:
    """Pull SIP SPY daily + per-session minute bars into data/research/alpaca/."""
    RESEARCH_ROOT.mkdir(parents=True, exist_ok=True)
    daily = fetch_daily_bars(settings, start=start, end=end)
    save_json(RESEARCH_ROOT / "spy_daily.json", {"bars": daily, "start": start.isoformat(), "end": end.isoformat()})
    paths = {"daily": str(RESEARCH_ROOT / "spy_daily.json")}
    for day in iter_trading_days(start, end):
        minute_path = RESEARCH_ROOT / "minute" / f"{day.isoformat()}.json"
        if minute_path.is_file():
            continue
        bars = fetch_stock_bars(settings, timeframe="1Min", start=day, end=day + timedelta(days=1)).get("SPY", [])
        if not bars:
            logger.warning("No minute bars for %s", day)
            continue
        save_json(minute_path, bars)
    paths["minute_dir"] = str(RESEARCH_ROOT / "minute")
    return paths


def load_daily_bars(cache_dir: Path | None = None) -> list[dict[str, Any]]:
    path = (cache_dir or RESEARCH_ROOT) / "spy_daily.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing real Alpaca daily cache at {path}; run ingest first.")
    return json.loads(path.read_text(encoding="utf-8")).get("bars") or []


def load_minute_bars(day: date, cache_dir: Path | None = None) -> list[dict[str, Any]]:
    path = (cache_dir or RESEARCH_ROOT) / "minute" / f"{day.isoformat()}.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))