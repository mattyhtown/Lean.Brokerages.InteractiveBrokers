from __future__ import annotations

import json
import logging
from datetime import date, time
from typing import Any

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.orats.client import ORATSClient, ORATS_NOTES
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.paths import ORATS_ROOT

logger = logging.getLogger(__name__)

SIGNAL_TIME = time(11, 0)
CLOSE_TIME = time(16, 0)


def require_orats_token(settings: Settings) -> str:
    token = settings.orats_api_token_resolved
    if not token:
        raise RuntimeError(
            "ORATS_API_TOKEN is not set. Export your token from https://orats.com/dashboard "
            "then run: export ORATS_API_TOKEN=..."
        )
    return token


def orats_rows_to_chain_candidates(rows: list[dict[str, str]], *, day: date) -> list[dict[str, Any]]:
    """Map ORATS strikes-chain CSV rows to research chain JSON (0DTE expiry only)."""
    day_s = day.isoformat()
    out: list[dict[str, Any]] = []
    for row in rows:
        expir = (row.get("expirDate") or "")[:10]
        if expir != day_s:
            continue
        strike = float(row.get("strike") or 0)
        if strike <= 0:
            continue
        yymmdd = day.strftime("%y%m%d")
        call_bid = _f(row.get("callBidPrice"))
        call_ask = _f(row.get("callAskPrice"))
        put_bid = _f(row.get("putBidPrice"))
        put_ask = _f(row.get("putAskPrice"))
        delta = _f(row.get("delta"))
        if call_bid is not None or call_ask is not None:
            out.append(
                {
                    "symbol": f"SPY{yymmdd}C{int(strike)}",
                    "strike": strike,
                    "right": "C",
                    "bid": call_bid,
                    "ask": call_ask,
                    "delta": abs(delta) if delta is not None else None,
                    "volume": _f(row.get("callVolume")) or 0.0,
                    "open_interest": _f(row.get("callOpenInterest")) or 0.0,
                }
            )
        if put_bid is not None or put_ask is not None:
            put_delta = -abs(delta) if delta is not None else None
            out.append(
                {
                    "symbol": f"SPY{yymmdd}P{int(strike)}",
                    "strike": strike,
                    "right": "P",
                    "bid": put_bid,
                    "ask": put_ask,
                    "delta": put_delta,
                    "volume": _f(row.get("putVolume")) or 0.0,
                    "open_interest": _f(row.get("putOpenInterest")) or 0.0,
                }
            )
    return out


def _f(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def ingest_range(
    settings: Settings,
    *,
    start: date,
    end: date,
    client: ORATSClient | None = None,
) -> dict[str, Any]:
    token = require_orats_token(settings)
    client = client or ORATSClient(token)
    ORATS_ROOT.mkdir(parents=True, exist_ok=True)
    meta_path = ORATS_ROOT / "ingest_notes.json"
    paths: dict[str, Any] = {
        "source": "orats",
        "notes": ORATS_NOTES.__dict__,
        "chain_dir": str(ORATS_ROOT.parent / "chains" / "SPY"),
        "days_written": 0,
        "days_skipped_empty": 0,
    }
    for day in iter_trading_days(start, end):
        signal_rows = client.strikes_chain_at(settings.underlying, day, at=SIGNAL_TIME)
        chain = orats_rows_to_chain_candidates(signal_rows, day=day)
        if not chain:
            paths["days_skipped_empty"] += 1
            logger.warning("ORATS returned no 0DTE chain rows for %s at 11:00 ET", day)
            continue
        save_chain_snapshot(chain, day, snapshot="signal", source="orats", extra={"orats_trade_time": "1100"})
        close_rows = client.strikes_chain_at(settings.underlying, day, at=CLOSE_TIME)
        close_chain = orats_rows_to_chain_candidates(close_rows, day=day)
        if close_chain:
            save_chain_snapshot(close_chain, day, snapshot="close", source="orats", extra={"orats_trade_time": "1600"})
        paths["days_written"] += 1
    meta_path.write_text(json.dumps(paths, indent=2), encoding="utf-8")
    return paths