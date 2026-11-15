from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.ib_client import IBSession
from odte_spy_ibkr.research.alpaca_ingest import save_json
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.paths import IBKR_ROOT, daily_spy_path, minute_path

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
# IB historical pacing: ~60 identical requests / 10 minutes — stay conservative.
IB_PACE_SEC = 11.0
SIGNAL_TIME = dt_time(11, 0)


def require_ib_connection(settings: Settings) -> IBSession:
    if settings.enable_orders and not settings.read_only:
        logger.warning("Research ingest forces read-only; no orders will be submitted.")
    session = IBSession(settings)
    try:
        session.connect()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Interactive Brokers session not available. Start TWS or IB Gateway with API enabled "
            f"({settings.ib_host}:{settings.ib_port}, client id {settings.ib_client_id}), "
            "set READ_ONLY=true, then retry."
        ) from exc
    if not session.is_connected():
        raise RuntimeError(
            f"IB connect() completed but session is not connected ({settings.ib_host}:{settings.ib_port})."
        )
    return session


def _bar_dict(bar: Any) -> dict[str, Any]:
    ts = bar.date
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    return {
        "t": ts.astimezone(ET).isoformat(),
        "o": float(bar.open),
        "h": float(bar.high),
        "l": float(bar.low),
        "c": float(bar.close),
        "v": float(bar.volume),
    }


def _fetch_spy_minutes(session: IBSession, day: date) -> list[dict[str, Any]]:
    from ib_insync import Stock

    stock = session.qualify_underlying()
    end = datetime.combine(day, dt_time(16, 0), tzinfo=ET)
    bars = session._ib.reqHistoricalData(
        stock,
        endDateTime=end,
        durationStr="1 D",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    session._ib.sleep(1)
    return [_bar_dict(b) for b in bars]


def _fetch_option_chain_at_signal(session: IBSession, settings: Settings, day: date, spot: float) -> list[dict[str, Any]]:
    from ib_insync import Option

    stock = session.qualify_underlying()
    expiry_str = day.strftime("%Y%m%d")
    chains = session._ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        return []
    chain = next((c for c in chains if c.exchange in ("SMART", "ARCA", "NASDAQ")), chains[0])
    strikes = sorted(s for s in chain.strikes if abs(s - spot) <= 20)[:30]
    rows: list[dict[str, Any]] = []
    end = datetime.combine(day, SIGNAL_TIME, tzinfo=ET) + timedelta(minutes=1)
    for strike in strikes:
        for right in ("P", "C"):
            contract = Option(stock.symbol, expiry_str, strike, right, chain.exchange)
            qualified = session._ib.qualifyContracts(contract)
            if not qualified:
                time.sleep(IB_PACE_SEC)
                continue
            contract = qualified[0]
            bars = session._ib.reqHistoricalData(
                contract,
                endDateTime=end,
                durationStr="1 D",
                barSizeSetting="1 min",
                whatToShow="BID_ASK",
                useRTH=True,
                formatDate=1,
            )
            session._ib.sleep(0.5)
            pick = None
            for b in reversed(bars):
                if b.date.hour == 11 and b.date.minute == 0:
                    pick = b
                    break
            if pick is None and bars:
                pick = bars[-1]
            if pick is None:
                time.sleep(IB_PACE_SEC)
                continue
            bid = float(getattr(pick, "close", 0) or 0)
            ask = bid
            rows.append(
                {
                    "symbol": contract.localSymbol or f"{stock.symbol}{expiry_str}{right}{strike}",
                    "strike": float(strike),
                    "right": right,
                    "bid": bid,
                    "ask": ask,
                    "delta": None,
                    "volume": float(pick.volume or 0),
                    "open_interest": 0.0,
                }
            )
            time.sleep(IB_PACE_SEC)
    return rows


def ingest_range(settings: Settings, *, start: date, end: date) -> dict[str, Any]:
    session = require_ib_connection(settings)
    IBKR_ROOT.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {"source": "ibkr", "days_minute": 0, "days_chain": 0, "pace_sec": IB_PACE_SEC}
    daily_closes: list[dict[str, Any]] = []
    try:
        for day in iter_trading_days(start, end):
            mp = minute_path(IBKR_ROOT, day)
            if not mp.is_file():
                minutes = _fetch_spy_minutes(session, day)
                if minutes:
                    save_json(mp, minutes)
                    stats["days_minute"] += 1
                    daily_closes.append({"t": f"{day.isoformat()}T16:00:00-05:00", "c": minutes[-1]["c"]})
            spot = None
            if mp.is_file():
                minutes = json.loads(mp.read_text(encoding="utf-8"))
                if minutes:
                    spot = float(minutes[min(len(minutes) // 2, len(minutes) - 1)]["c"])
            if spot is None:
                spot = session.get_spot_price() or 0.0
            chain = _fetch_option_chain_at_signal(session, settings, day, spot)
            if chain:
                save_chain_snapshot(chain, day, snapshot="signal", source="ibkr")
                stats["days_chain"] += 1
            time.sleep(IB_PACE_SEC)
    finally:
        session.disconnect()
    if daily_closes:
        save_json(daily_spy_path(IBKR_ROOT), {"bars": daily_closes, "start": start.isoformat(), "end": end.isoformat()})
    (IBKR_ROOT / "ingest_notes.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats