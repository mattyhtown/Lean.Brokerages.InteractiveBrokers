from __future__ import annotations

import json
import logging
import time
from datetime import date, time as dt_time
from pathlib import Path
from typing import Any

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.features import SIGNAL_TIME
from odte_spy_ibkr.research.paths import CHAIN_ROOT
from odte_spy_ibkr.thetadata.client import ThetaDataClient, ThetaTerminalNotRunning

logger = logging.getLogger(__name__)

THETADATA_ROOT = Path("data/research/thetadata")
SIGNAL_END = dt_time(11, 5)
CLOSE_WINDOW = (dt_time(15, 55), dt_time(16, 5))

THETA_DOCS = (
    "Theta Data historical options require Theta Terminal v3 running locally "
    "(loopback REST, default http://127.0.0.1:25503). "
    "Launch: `java -jar ThetaTerminalv3.jar <email> <password>` or API key per "
    "https://http-docs.thetadata.us/Articles/Getting-Started/Getting-Started.html"
)


def has_thetadata_credentials(settings: Settings) -> bool:
    return bool(settings.thetadata_username.strip() and settings.thetadata_password.strip())


def _theta_rows_to_chain(quotes: list[dict[str, Any]], *, day: date) -> list[dict[str, Any]]:
    """Pick the last quote per (strike, right) in the window."""
    buckets: dict[tuple[float, str], dict[str, Any]] = {}
    for q in quotes:
        strike = _strike(q)
        right = _right(q)
        if strike is None or right is None:
            continue
        key = (strike, right)
        prev = buckets.get(key)
        if prev is None or _ts_rank(q) >= _ts_rank(prev):
            buckets[key] = q
    rows: list[dict[str, Any]] = []
    yymmdd = day.strftime("%y%m%d")
    for (strike, right), q in sorted(buckets.items()):
        bid = _float(q.get("bid"))
        ask = _float(q.get("ask"))
        if bid is None and ask is None:
            continue
        if bid is None:
            bid = ask
        if ask is None:
            ask = bid
        rows.append(
            {
                "symbol": q.get("symbol") or f"SPY{yymmdd}{right}{int(round(strike * 1000)):08d}",
                "strike": strike,
                "right": right,
                "bid": bid,
                "ask": ask,
                "delta": _float(q.get("delta")),
                "volume": _float(q.get("volume")) or 0.0,
                "open_interest": _float(q.get("open_interest")) or 0.0,
            }
        )
    return rows


def _float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _strike(q: dict[str, Any]) -> float | None:
    for key in ("strike", "strike_price", "Strike"):
        if key in q:
            return _float(q[key])
    return None


def _right(q: dict[str, Any]) -> str | None:
    raw = str(q.get("right") or q.get("Right") or "").lower()
    if raw in {"c", "call"}:
        return "C"
    if raw in {"p", "put"}:
        return "P"
    sym = str(q.get("symbol") or "")
    if "C" in sym[-10:]:
        return "C"
    if "P" in sym[-10:]:
        return "P"
    return None


def _ts_rank(q: dict[str, Any]) -> int:
    ms = q.get("ms_of_day") or q.get("msOfDay")
    if ms is not None:
        try:
            return int(ms)
        except (TypeError, ValueError):
            pass
    return 0


def ingest_range(settings: Settings, *, start: date, end: date) -> dict[str, Any]:
    if not has_thetadata_credentials(settings):
        raise RuntimeError(
            "THETADATA_USERNAME and THETADATA_PASSWORD must be set to run Theta ingest "
            f"(terminal must be running locally). {THETA_DOCS}"
        )
    client = ThetaDataClient(base_url=settings.thetadata_terminal_url)
    try:
        client.require_terminal()
    except ThetaTerminalNotRunning:
        raise RuntimeError(
            f"{THETA_DOCS} Credentials are present but the terminal is not reachable at "
            f"{settings.thetadata_terminal_url}."
        ) from None

    stats = {"days_chain": 0, "days_skipped": 0, "days_empty": 0, "symbol": settings.underlying}
    for day in iter_trading_days(start, end):
        sig_path = CHAIN_ROOT / f"{day.isoformat()}.json"
        if sig_path.is_file():
            stats["days_skipped"] += 1
            continue
        exp = day
        signal_q = client.option_history_quote(
            symbol=settings.underlying,
            expiration=exp,
            quote_date=day,
            start_time=SIGNAL_TIME,
            end_time=SIGNAL_END,
            strike_range=settings.thetadata_strike_range,
            max_dte=0,
        )
        time.sleep(0.1)
        close_q = client.option_history_quote(
            symbol=settings.underlying,
            expiration=exp,
            quote_date=day,
            start_time=CLOSE_WINDOW[0],
            end_time=CLOSE_WINDOW[1],
            strike_range=settings.thetadata_strike_range,
            max_dte=0,
        )
        signal_rows = _theta_rows_to_chain(signal_q, day=day)
        if not signal_rows:
            stats["days_empty"] += 1
            continue
        close_rows = _theta_rows_to_chain(close_q, day=day)
        save_chain_snapshot(signal_rows, day, snapshot="signal", source="thetadata")
        if close_rows:
            save_chain_snapshot(close_rows, day, snapshot="close", source="thetadata")
        stats["days_chain"] += 1
    THETADATA_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = THETADATA_ROOT / "ingest_summary.json"
    manifest.write_text(json.dumps({**stats, "start": start.isoformat(), "end": end.isoformat()}, indent=2))
    return stats