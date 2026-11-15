from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.alphavantage.co/query"

AV_API_NOTES = """
Alpha Vantage (free tier) limits:
- ~25 API calls/day and 5 calls/minute on the standard free key (check your plan on alphavantage.co).
- TIME_SERIES_INTRADAY (1min): one call returns many sessions; ingest splits bars into per-day JSON under
  data/research/alphavantage/minute/ (no per-day API loop).
- TIME_SERIES_DAILY_ADJUSTED: one call for full daily history in range filter.
- Options REALTIME_OPTIONS: current chain snapshot only on many plans.
- HISTORICAL_OPTIONS (dated 0DTE): often premium / entitlement-gated — ingest records empty days, never synthesizes.
""".strip()


class AlphaVantageError(Exception):
    pass


def is_configured(api_key: str) -> bool:
    return bool(api_key.strip())


def _check_av_error(payload: dict[str, Any]) -> None:
    if "Error Message" in payload:
        raise AlphaVantageError(str(payload["Error Message"]))
    if "Information" in payload:
        raise AlphaVantageError(str(payload["Information"]))
    if "Note" in payload:
        raise AlphaVantageError(str(payload["Note"]))


def av_query(
    *,
    api_key: str,
    params: dict[str, Any],
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
    get_json: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not is_configured(api_key):
        raise AlphaVantageError("ALPHAVANTAGE_API_KEY is not set")
    merged = {**params, "apikey": api_key.strip()}
    if get_json is not None:
        payload = get_json(base_url, merged)
    elif client is not None:
        resp = client.get(base_url, params=merged)
        resp.raise_for_status()
        payload = resp.json()
    else:
        with httpx.Client(timeout=60.0) as c:
            resp = c.get(base_url, params=merged)
            resp.raise_for_status()
            payload = resp.json()
    if not isinstance(payload, dict):
        raise AlphaVantageError("unexpected response shape")
    _check_av_error(payload)
    return payload


def fetch_global_quote(
    *,
    symbol: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
    get_json: Callable[..., Any] | None = None,
) -> float | None:
    payload = av_query(
        api_key=api_key,
        base_url=base_url,
        params={"function": "GLOBAL_QUOTE", "symbol": symbol},
        client=client,
        get_json=get_json,
    )
    quote = payload.get("Global Quote") or {}
    raw = quote.get("05. price") or quote.get("price")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fetch_time_series_intraday(
    *,
    symbol: str,
    api_key: str,
    interval: str = "1min",
    outputsize: str = "full",
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
    get_json: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return av_query(
        api_key=api_key,
        base_url=base_url,
        params={
            "function": "TIME_SERIES_INTRADAY",
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "adjusted": "true",
            "extended_hours": "false",
        },
        client=client,
        get_json=get_json,
    )


def fetch_time_series_daily(
    *,
    symbol: str,
    api_key: str,
    outputsize: str = "full",
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
    get_json: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return av_query(
        api_key=api_key,
        base_url=base_url,
        params={
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": outputsize,
        },
        client=client,
        get_json=get_json,
    )


def fetch_realtime_options(
    *,
    symbol: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
    get_json: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return av_query(
        api_key=api_key,
        base_url=base_url,
        params={"function": "REALTIME_OPTIONS", "symbol": symbol},
        client=client,
        get_json=get_json,
    )


def fetch_historical_options(
    *,
    symbol: str,
    day: date,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
    get_json: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return av_query(
        api_key=api_key,
        base_url=base_url,
        params={
            "function": "HISTORICAL_OPTIONS",
            "symbol": symbol,
            "date": day.strftime("%Y%m%d"),
        },
        client=client,
        get_json=get_json,
    )


def normalize_intraday_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    key = next((k for k in payload if k.startswith("Time Series")), None)
    if not key:
        return []
    series = payload.get(key) or {}
    rows: list[dict[str, Any]] = []
    for ts_str, ohlc in series.items():
        if not isinstance(ohlc, dict):
            continue
        try:
            o = float(ohlc.get("1. open", ohlc.get("open", 0)))
            h = float(ohlc.get("2. high", ohlc.get("high", 0)))
            l = float(ohlc.get("3. low", ohlc.get("low", 0)))
            c = float(ohlc.get("4. close", ohlc.get("close", 0)))
            v = float(ohlc.get("5. volume", ohlc.get("volume", 0)))
        except (TypeError, ValueError):
            continue
        # Alpaca-compatible keys for research feature pipeline
        iso = ts_str.replace(" ", "T") + "-05:00"
        rows.append({"t": iso, "o": o, "h": h, "l": l, "c": c, "v": v})
    rows.sort(key=lambda b: b["t"])
    return rows


def normalize_daily_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    key = next((k for k in payload if "Daily" in k), None)
    if not key:
        return []
    series = payload.get(key) or {}
    rows: list[dict[str, Any]] = []
    for day_s, ohlc in series.items():
        if not isinstance(ohlc, dict):
            continue
        try:
            rows.append(
                {
                    "t": f"{day_s}T00:00:00Z",
                    "o": float(ohlc.get("1. open", 0)),
                    "h": float(ohlc.get("2. high", 0)),
                    "l": float(ohlc.get("3. low", 0)),
                    "c": float(ohlc.get("4. close", 0)),
                    "v": float(ohlc.get("6. volume", ohlc.get("5. volume", 0))),
                }
            )
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda b: b["t"])
    return rows


def split_intraday_by_session_date(bars: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for bar in bars:
        t = bar.get("t", "")
        day_s = t[:10]
        if not day_s:
            continue
        by_day.setdefault(day_s, []).append(bar)
    return by_day


def throttle_between_calls(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)