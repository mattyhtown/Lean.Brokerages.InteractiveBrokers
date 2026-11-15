from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from odte_spy_ibkr.chain import OptionCandidate, today_expiry
from odte_spy_ibkr.chain.enrich import enrich_chain_rows
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research.chain_io import load_chain_rows, rows_to_candidates
from odte_spy_ibkr.research.paths import CHAIN_ROOT, OPENBB_CHAIN_ROOT


def _cache_path(settings: Settings, ticker: str, day: date) -> Path:
    return Path(settings.backtest_cache_dir) / ticker / f"{day.isoformat()}.parquet"


def load_chain_for_day(
    settings: Settings,
    *,
    ticker: str,
    day: date,
    spot: float | None = None,
) -> list[OptionCandidate]:
    if ticker.upper() == settings.underlying.upper():
        research_rows = load_chain_rows(day, snapshot="signal", root=CHAIN_ROOT)
        if not research_rows:
            research_rows = load_chain_rows(day, snapshot="signal", root=OPENBB_CHAIN_ROOT)
        if research_rows:
            if spot is not None and spot > 0 and any(r.get("delta") is None for r in research_rows):
                research_rows, _ = enrich_chain_rows(research_rows, day=day, spot=spot)
            return rows_to_candidates(research_rows, day)

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
    _persist_chain_rows(settings, path, json_fallback, fetched)
    return _rows_to_candidates(fetched, day)


def _persist_chain_rows(
    settings: Settings,
    parquet_path: Path,
    json_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    try:
        import pandas as pd

        pd.DataFrame(rows).to_parquet(parquet_path, index=False)
        return
    except Exception:  # noqa: BLE001
        pass
    json_path.write_text(json.dumps(rows), encoding="utf-8")


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
    """$1-spaced strikes so SPREAD_WIDTH=1 vertical spreads can form."""
    rows: list[dict[str, Any]] = []
    center = int(round(spot))
    for strike in range(center - 15, center + 16):
        for right in ("P", "C"):
            if right == "P":
                otm = max(0.0, spot - strike)
                bid = max(0.05, 0.75 - otm * 0.05)
                delta = min(0.35, 0.08 + otm * 0.012)
            else:
                otm = max(0.0, strike - spot)
                bid = max(0.05, 0.75 - otm * 0.05)
                delta = min(0.35, 0.08 + otm * 0.012)
            rows.append(
                {
                    "symbol": f"SPY{day.strftime('%y%m%d')}{right}{strike}",
                    "strike": float(strike),
                    "right": right,
                    "bid": bid,
                    "ask": bid + 0.04,
                    "delta": delta,
                    "volume": 100 + int(otm) * 15,
                    "open_interest": 400 + int(otm) * 40,
                }
            )
    puts = {r["strike"]: r for r in rows if r["right"] == "P"}
    for strike, short in puts.items():
        long_row = puts.get(strike - 1)
        if long_row is not None:
            long_row["ask"] = max(0.05, float(short["bid"]) - 0.18)
    calls = {r["strike"]: r for r in rows if r["right"] == "C"}
    for strike, short in calls.items():
        long_row = calls.get(strike + 1)
        if long_row is not None:
            long_row["ask"] = max(0.05, float(short["bid"]) - 0.18)
    return rows