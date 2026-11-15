from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from odte_spy_ibkr.alphavantage.client import (
    AV_API_NOTES,
    AlphaVantageError,
    fetch_historical_options,
    fetch_realtime_options,
    fetch_time_series_daily,
    fetch_time_series_intraday,
    is_configured,
    normalize_daily_rows,
    normalize_intraday_rows,
    split_intraday_by_session_date,
    throttle_between_calls,
)
from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research.alpaca_ingest import save_json
from odte_spy_ibkr.research.paths import ALPHAVANTAGE_ROOT

logger = logging.getLogger(__name__)


def require_alphavantage_key(settings: Settings) -> None:
    if not is_configured(settings.alphavantage_api_key):
        raise RuntimeError(
            "ALPHAVANTAGE_API_KEY is required for Alpha Vantage ingest "
            "(set in .env or Cursor cloud secrets)."
        )


def ingest_range(settings: Settings, *, start: date, end: date) -> dict[str, Any]:
    """
    Pull SPY daily + 1min (split per session) into data/research/alphavantage/.
    Optional dated options via HISTORICAL_OPTIONS (often premium; empty days are recorded).
    """
    require_alphavantage_key(settings)
    stats: dict[str, Any] = {
        "days_minute_written": 0,
        "days_minute_missing": 0,
        "days_options_written": 0,
        "days_options_empty": 0,
        "api_calls": 0,
        "notes": AV_API_NOTES,
    }
    root = ALPHAVANTAGE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    minute_dir = root / "minute"
    minute_dir.mkdir(parents=True, exist_ok=True)
    options_dir = root / "options" / settings.underlying
    options_dir.mkdir(parents=True, exist_ok=True)

    daily_path = root / "spy_daily.json"
    if not daily_path.is_file():
        payload = fetch_time_series_daily(
            symbol=settings.underlying,
            api_key=settings.alphavantage_api_key,
            base_url=settings.alphavantage_base_url,
            outputsize=settings.alphavantage_outputsize,
        )
        stats["api_calls"] += 1
        daily_rows = [r for r in normalize_daily_rows(payload) if start <= date.fromisoformat(r["t"][:10]) <= end]
        save_json(daily_path, {"bars": daily_rows, "start": start.isoformat(), "end": end.isoformat()})
        throttle_between_calls(settings.alphavantage_request_delay_sec)

    need_minute = any(
        not (minute_dir / f"{day.isoformat()}.json").is_file() for day in iter_trading_days(start, end)
    )
    by_day: dict[str, list[dict[str, Any]]] = {}
    if need_minute:
        intraday = fetch_time_series_intraday(
            symbol=settings.underlying,
            api_key=settings.alphavantage_api_key,
            base_url=settings.alphavantage_base_url,
            interval="1min",
            outputsize=settings.alphavantage_outputsize,
        )
        stats["api_calls"] += 1
        all_bars = normalize_intraday_rows(intraday)
        by_day = split_intraday_by_session_date(all_bars)
        throttle_between_calls(settings.alphavantage_request_delay_sec)

    for day in iter_trading_days(start, end):
        day_s = day.isoformat()
        minute_path = minute_dir / f"{day_s}.json"
        if minute_path.is_file():
            continue
        bars = by_day.get(day_s, [])
        if bars:
            save_json(minute_path, bars)
            stats["days_minute_written"] += 1
        else:
            stats["days_minute_missing"] += 1

    if settings.alphavantage_ingest_options:
        for day in iter_trading_days(start, end):
            opt_path = options_dir / f"{day.isoformat()}.json"
            if opt_path.is_file():
                continue
            try:
                payload = fetch_historical_options(
                    symbol=settings.underlying,
                    day=day,
                    api_key=settings.alphavantage_api_key,
                    base_url=settings.alphavantage_base_url,
                )
                stats["api_calls"] += 1
                throttle_between_calls(settings.alphavantage_request_delay_sec)
                if payload:
                    save_json(opt_path, {"date": day.isoformat(), "source": "alphavantage", "raw": payload})
                    stats["days_options_written"] += 1
                else:
                    stats["days_options_empty"] += 1
            except AlphaVantageError as exc:
                logger.warning("HISTORICAL_OPTIONS %s: %s", day, exc)
                stats["days_options_empty"] += 1
                if "premium" in str(exc).lower() or "rate" in str(exc).lower():
                    break
        if settings.alphavantage_ingest_realtime_options and not (options_dir / "realtime.json").is_file():
            try:
                rt = fetch_realtime_options(
                    symbol=settings.underlying,
                    api_key=settings.alphavantage_api_key,
                    base_url=settings.alphavantage_base_url,
                )
                stats["api_calls"] += 1
                save_json(options_dir / "realtime.json", {"source": "realtime_options", "raw": rt})
            except AlphaVantageError as exc:
                logger.warning("REALTIME_OPTIONS skipped: %s", exc)

    save_json(root / "ingest_summary.json", stats)
    return stats