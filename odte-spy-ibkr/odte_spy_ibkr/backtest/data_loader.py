from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from odte_spy_ibkr.backtest.cache_audit import chain_snapshot_problem, is_bar_rows
from odte_spy_ibkr.backtest.store import read_rows
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
    rows = read_rows(path)
    if not rows or is_bar_rows(rows):
        # Nothing cached, or 1-minute option bars (replayed by backtest.session_data).
        return []
    # Only a snapshot captured during `day`'s own session is that day's chain. There is no
    # fallback: a day without one has no chain, and the caller skips it.
    problem = chain_snapshot_problem(rows, day)
    if problem:
        logging.getLogger(__name__).warning("ignoring cached chain %s: %s", path, problem)
        return []
    return _rows_to_candidates(rows, day)


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
