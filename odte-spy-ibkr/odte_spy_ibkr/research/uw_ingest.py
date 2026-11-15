from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from typing import Any

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.paths import UW_ROOT, chain_path
from odte_spy_ibkr.unusual_whales.client import UnusualWhalesClient
from odte_spy_ibkr.unusual_whales.signals import aggregate_flow_bias

logger = logging.getLogger(__name__)

# Public docs: https://api.unusualwhales.com/docs
# Historical-by-day: option-chains?date=, flow-per-strike?date=
# flow-recent / flow-alerts: latest only (not historical backfill per UW docs)
UW_HISTORICAL_ENDPOINTS = [
    "GET /api/stock/{ticker}/option-chains?date=&greeks=true",
    "GET /api/stock/{ticker}/flow-per-strike?date=",
]
UW_LIVE_ONLY_ENDPOINTS = [
    "GET /api/stock/{ticker}/flow-recent",
    "GET /api/stock/{ticker}/flow-alerts (deprecated)",
]
UW_PLAN_NOTE = (
    "Startup tier documents 160K req/day, 5 concurrent, 2-year lookback (enterprise_startup). "
    "Depth per endpoint depends on your subscription — 402/403 responses mean tier limits."
)


def require_uw_key(settings: Settings) -> str:
    key = settings.unusual_whales_api_key.strip()
    if not key:
        raise RuntimeError(
            "UNUSUAL_WHALES_API_KEY is not set. Create a token at https://unusualwhales.com/dashboard/api "
            "then export UNUSUAL_WHALES_API_KEY=..."
        )
    return key


def _parse_uw_chain_rows(payload: Any, *, day: date) -> list[dict[str, Any]]:
    if payload is None:
        return []
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    yymmdd = day.strftime("%y%m%d")
    for item in data:
        if isinstance(item, str):
            continue
        opt_type = str(item.get("type") or item.get("option_type") or "").upper()
        right = "C" if opt_type in {"C", "CALL"} else "P" if opt_type in {"P", "PUT"} else ""
        if not right:
            sym = str(item.get("option_symbol") or item.get("symbol") or "")
            right = "C" if "C" in sym[-9:] else "P" if "P" in sym[-9:] else ""
        strike_raw = item.get("strike") or item.get("strike_price")
        if strike_raw is None:
            continue
        strike = float(strike_raw)
        symbol = str(item.get("option_symbol") or item.get("symbol") or f"SPY{yymmdd}{right}{int(strike)}")
        rows.append(
            {
                "symbol": symbol,
                "strike": strike,
                "right": right,
                "bid": _num(item.get("nbbo_bid") or item.get("bid")),
                "ask": _num(item.get("nbbo_ask") or item.get("ask")),
                "delta": _num(item.get("delta")),
                "volume": _num(item.get("volume")) or 0.0,
                "open_interest": _num(item.get("open_interest") or item.get("oi")) or 0.0,
            }
        )
    return [r for r in rows if r.get("right") in {"C", "P"}]


def _num(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def ingest_range(
    settings: Settings,
    *,
    start: date,
    end: date,
    client: UnusualWhalesClient | None = None,
    sleep_sec: float = 0.25,
) -> dict[str, Any]:
    require_uw_key(settings)
    client = client or UnusualWhalesClient(
        settings.unusual_whales_api_key,
        base_url=settings.uw_base_url,
        cache_ttl_sec=0.0,
    )
    ticker = settings.underlying
    daily_dir = UW_ROOT / "daily"
    sessions_dir = UW_ROOT / "sessions"
    daily_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {
        "source": "unusual_whales",
        "historical_endpoints": UW_HISTORICAL_ENDPOINTS,
        "live_only_endpoints": UW_LIVE_ONLY_ENDPOINTS,
        "plan_note": UW_PLAN_NOTE,
        "days_written": 0,
        "chain_days": 0,
    }
    for day in iter_trading_days(start, end):
        day_s = day.isoformat()
        flow = client.flow_per_strike(ticker, date=day_s)
        time.sleep(sleep_sec)
        chain_pages: list[dict[str, Any]] = []
        page = 0
        while True:
            chains = client.option_chains(ticker, date=day_s, greeks=True, page=page)
            if not chains:
                break
            chain_pages.append(chains)
            total_pages = (chains.get("total_pages") if isinstance(chains, dict) else None) or 1
            page += 1
            if page >= int(total_pages):
                break
            time.sleep(sleep_sec)
        bundle = {
            "date": day_s,
            "flow_per_strike": flow,
            "option_chains_pages": chain_pages,
        }
        bias = aggregate_flow_bias(bundle)
        daily_path = daily_dir / f"{day_s}.json"
        daily_path.write_text(
            json.dumps({"date": day_s, "aggregate_bias": bias, "bundle_keys": list(bundle.keys())}, indent=2),
            encoding="utf-8",
        )
        snap = {"collected_at": datetime.now(tz=timezone.utc).isoformat(), "bundle": bundle}
        jsonl = sessions_dir / f"{day_s}.jsonl"
        with jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap) + "\n")
        chain_rows: list[dict[str, Any]] = []
        for page_payload in chain_pages:
            chain_rows.extend(_parse_uw_chain_rows(page_payload, day=day))
        if chain_rows:
            save_chain_snapshot(chain_rows, day, snapshot="signal", source="unusual_whales")
            stats["chain_days"] += 1
        stats["days_written"] += 1
    (UW_ROOT / "ingest_notes.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats