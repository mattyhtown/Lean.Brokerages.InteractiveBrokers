from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from odte_spy_ibkr.chain import OptionCandidate, today_expiry
from odte_spy_ibkr.config import Settings


def _cache_path(settings: Settings, ticker: str, day: date) -> Path:
    return Path(settings.backtest_cache_dir) / ticker / f"{day.isoformat()}.parquet"


def load_chain_for_day(
    settings: Settings,
    *,
    ticker: str,
    day: date,
    spot: float | None = None,
) -> list[OptionCandidate]:
    path = _cache_path(settings, ticker, day)
    json_fallback = path.with_suffix(".json")
    if json_fallback.is_file():
        rows = json.loads(json_fallback.read_text(encoding="utf-8"))
        return _rows_to_candidates(rows, day)

    if path.is_file():
        try:
            import pandas as pd

            df = pd.read_parquet(path)
            rows = df.to_dict(orient="records")
            return _rows_to_candidates(rows, day)
        except Exception:  # noqa: BLE001
            pass

    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        return _synthetic_chain(day, spot or 450.0)

    fetched = _fetch_alpaca_chain(settings, ticker=ticker, day=day)
    path.parent.mkdir(parents=True, exist_ok=True)
    json_fallback.write_text(json.dumps(fetched), encoding="utf-8")
    return _rows_to_candidates(fetched, day)


def _rows_to_candidates(rows: list[dict[str, Any]], day: date) -> list[OptionCandidate]:
    expiry = today_expiry(day)
    out: list[OptionCandidate] = []
    for row in rows:
        out.append(
            OptionCandidate(
                symbol=str(row.get("symbol", "")),
                strike=float(row["strike"]),
                right=row["right"],
                expiry=expiry,
                delta=float(row["delta"]) if row.get("delta") is not None else None,
                bid=float(row["bid"]) if row.get("bid") is not None else None,
                ask=float(row["ask"]) if row.get("ask") is not None else None,
                volume=float(row.get("volume") or 0),
                open_interest=float(row.get("open_interest") or 0),
            )
        )
    return out


def _fetch_alpaca_chain(settings: Settings, *, ticker: str, day: date) -> list[dict[str, Any]]:
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }
    url = f"{settings.alpaca_data_url.rstrip('/')}/v1beta1/options/snapshots/{ticker}"
    with httpx.Client(timeout=25.0) as client:
        resp = client.get(url, headers=headers, params={"feed": "indicative", "limit": 200})
        if resp.status_code >= 400:
            return []
        payload = resp.json()
    snapshots = payload.get("snapshots") or payload
    rows: list[dict[str, Any]] = []
    if isinstance(snapshots, dict):
        for sym, snap in snapshots.items():
            rows.append(_snapshot_row(sym, snap))
    return rows or _synthetic_chain_rows(day, 450.0)


def _snapshot_row(symbol: str, snap: dict[str, Any]) -> dict[str, Any]:
    quote = snap.get("latestQuote") or snap.get("latest_quote") or {}
    greeks = snap.get("greeks") or {}
    right = "C" if "C" in symbol else "P"
    strike = _parse_strike(symbol)
    return {
        "symbol": symbol,
        "strike": strike,
        "right": right,
        "bid": quote.get("bp") or quote.get("bid"),
        "ask": quote.get("ap") or quote.get("ask"),
        "delta": greeks.get("delta"),
        "volume": snap.get("volume") or snap.get("v") or 0,
        "open_interest": snap.get("open_interest") or 0,
    }


def _parse_strike(symbol: str) -> float:
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) >= 8:
        return float(digits[-8:]) / 1000.0
    return 0.0


def _synthetic_chain(day: date, spot: float) -> list[OptionCandidate]:
    return _rows_to_candidates(_synthetic_chain_rows(day, spot), day)


def _synthetic_chain_rows(day: date, spot: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in (-5, -3, -1, 1, 3, 5):
        strike = round(spot + offset, 0)
        for right in ("P", "C"):
            rows.append(
                {
                    "symbol": f"SPY{day.strftime('%y%m%d')}{right}{int(strike)}",
                    "strike": strike,
                    "right": right,
                    "bid": 0.4,
                    "ask": 0.45,
                    "delta": 0.15 if right == "P" else 0.12,
                    "volume": 100 + abs(offset) * 20,
                    "open_interest": 500 + abs(offset) * 50,
                }
            )
    return rows