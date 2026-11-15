from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.chain.enrich import enrich_chain_rows
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.openbb.client import (
    OPENBB_HISTORICAL_NOTES,
    configure_openbb_session,
    fetch_option_chains,
    openbb_available,
    require_openbb,
)
from odte_spy_ibkr.research.alpaca_ingest import save_json
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.features import SIGNAL_TIME, _price_at
from odte_spy_ibkr.research.minute_io import load_minute_bars
from odte_spy_ibkr.research.paths import CHAIN_ROOT, OPENBB_ROOT

logger = logging.getLogger(__name__)


def require_openbb_credentials(settings: Settings) -> None:
    require_openbb()
    if not settings.openbb_pat.strip() and settings.openbb_historical_provider != "cboe":
        logger.warning(
            "OPENBB_PAT not set; historical provider %s may fail. Live providers cboe/yfinance work without PAT.",
            settings.openbb_historical_provider,
        )


def ingest_range(settings: Settings, *, start: date, end: date) -> dict[str, Any]:
    """
    Pull 0DTE chains via OpenBB into data/research/openbb/chains/SPY/.
    Also writes data/research/chains/SPY/{date}.json when no file exists yet.
    """
    require_openbb_credentials(settings)
    configure_openbb_session(settings.openbb_pat)
    stats: dict[str, Any] = {
        "days_chain": 0,
        "days_empty": 0,
        "days_skipped_existing": 0,
        "days_no_minute_spot": 0,
        "provider": settings.openbb_historical_provider,
        "notes": OPENBB_HISTORICAL_NOTES,
    }
    chain_dir = OPENBB_ROOT / "chains" / settings.underlying
    chain_dir.mkdir(parents=True, exist_ok=True)

    for day in iter_trading_days(start, end):
        unified = CHAIN_ROOT / f"{day.isoformat()}.json"
        if unified.is_file():
            stats["days_skipped_existing"] += 1
            continue
        minute = load_minute_bars(day)
        spot = _price_at(minute, SIGNAL_TIME) if minute else None
        if not spot or spot <= 0:
            stats["days_no_minute_spot"] += 1
            continue
        rows = fetch_option_chains(
            symbol=settings.underlying,
            day=day,
            provider=settings.openbb_historical_provider,
        )
        if not rows:
            stats["days_empty"] += 1
            continue
        rows, enrich_stats = enrich_chain_rows(rows, day=day, spot=spot)
        save_chain_snapshot(rows, day, snapshot="signal", source="openbb", extra={"enrich": enrich_stats})
        openbb_path = chain_dir / f"{day.isoformat()}.json"
        save_json(
            openbb_path,
            {
                "date": day.isoformat(),
                "source": "openbb",
                "provider": settings.openbb_historical_provider,
                "rows": rows,
                "enrich": enrich_stats,
            },
        )
        stats["days_chain"] += 1

    manifest = OPENBB_ROOT / "ingest_summary.json"
    save_json(manifest, stats)
    return stats


def ingest_live_snapshot(settings: Settings, *, day: date | None = None) -> list[dict[str, Any]]:
    """Current chain for live/provider use (no historical date)."""
    require_openbb()
    configure_openbb_session(settings.openbb_pat)
    return fetch_option_chains(
        symbol=settings.underlying,
        day=day,
        provider=settings.openbb_live_provider if day is None else settings.openbb_historical_provider,
    )