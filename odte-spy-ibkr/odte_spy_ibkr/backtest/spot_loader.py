from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx

from odte_spy_ibkr.config import Settings


def _bar_cache_path(settings: Settings, ticker: str, day: date) -> Path:
    return Path(settings.backtest_cache_dir) / "bars" / ticker / f"{day.isoformat()}.parquet"


def _bar_json_fallback(settings: Settings, ticker: str, day: date) -> Path:
    return Path(settings.backtest_cache_dir) / "bars" / ticker / f"{day.isoformat()}.json"


def load_spot_for_day(settings: Settings, *, ticker: str, day: date, default: float = 450.0) -> float:
    """Spot at ~11:00 ET using cached Alpaca daily bar or synthetic default."""
    cache = _bar_cache_path(settings, ticker, day)
    json_fb = _bar_json_fallback(settings, ticker, day)
    if cache.is_file():
        try:
            import pandas as pd

            row = pd.read_parquet(cache).iloc[0].to_dict()
            for key in ("close", "c", "close_price"):
                if key in row and row[key] is not None:
                    return float(row[key])
        except Exception:  # noqa: BLE001
            pass
    if json_fb.is_file():
        data = json.loads(json_fb.read_text(encoding="utf-8"))
        for key in ("close", "c", "close_price"):
            if key in data:
                return float(data[key])

    if settings.alpaca_api_key and settings.alpaca_api_secret:
        bar = _fetch_alpaca_daily_bar(settings, ticker, day)
        if bar:
            cache.parent.mkdir(parents=True, exist_ok=True)
            try:
                import pandas as pd

                pd.DataFrame([bar]).to_parquet(cache, index=False)
            except Exception:  # noqa: BLE001
                json_fb.parent.mkdir(parents=True, exist_ok=True)
                json_fb.write_text(json.dumps(bar), encoding="utf-8")
            return float(bar.get("c") or bar.get("close") or default)

    return default


def _fetch_alpaca_daily_bar(settings: Settings, ticker: str, day: date) -> dict | None:
    url = f"{settings.alpaca_data_url.rstrip('/')}/v2/stocks/{ticker}/bars"
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }
    params = {
        "start": day.isoformat(),
        "end": day.isoformat(),
        "timeframe": "1Day",
        "limit": 1,
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            bars = resp.json().get("bars") or []
            return bars[0] if bars else None
    except httpx.HTTPError:
        return None