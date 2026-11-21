from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from odte_spy_ibkr.backtest.store import write_rows
from odte_spy_ibkr.chain import OptionCandidate

ET = ZoneInfo("America/New_York")


def save_chain_json(
    candidates: list[OptionCandidate],
    *,
    base_dir: str | Path,
    ticker: str,
    day: date | None = None,
    snapshot_at: datetime | None = None,
) -> Path:
    """Persist the session's first chain snapshot with its capture time.

    A snapshot is only ever filed under the date it was captured on.

    Later polls do not overwrite it: the backtest replays the snapshot at `snapshot_at`,
    so it must stay the quotes that were actually observable then.
    """
    captured = snapshot_at or datetime.now(tz=timezone.utc)
    captured_day = captured.astimezone(ET).date()
    if day is not None and day != captured_day:
        raise ValueError(f"chain snapshot captured on {captured_day} cannot be stored as {day}")
    day = captured_day
    parquet_path = Path(base_dir) / ticker / f"{day.isoformat()}.parquet"
    for existing in (parquet_path, parquet_path.with_suffix(".json")):
        if existing.is_file():
            return existing
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = captured.isoformat()
    rows: list[dict[str, Any]] = []
    for c in candidates:
        rows.append(
            {
                "symbol": c.symbol,
                "strike": c.strike,
                "right": c.right,
                "bid": c.bid,
                "ask": c.ask,
                "delta": c.delta,
                "volume": c.volume,
                "open_interest": c.open_interest,
                "snapshot_at": stamp,
            }
        )
    return write_rows(parquet_path, rows)
