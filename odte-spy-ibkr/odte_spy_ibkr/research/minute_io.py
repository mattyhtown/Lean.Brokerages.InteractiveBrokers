from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from odte_spy_ibkr.research.paths import ALPACA_ROOT, ALPHAVANTAGE_ROOT, IBKR_ROOT, QUANTCONNECT_ROOT

MINUTE_SOURCES: tuple[Path, ...] = (ALPACA_ROOT, QUANTCONNECT_ROOT, ALPHAVANTAGE_ROOT, IBKR_ROOT)


def load_minute_bars(day: date, cache_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load SPY minute bars; prefer explicit cache_dir, else first hit among research roots."""
    if cache_dir is not None:
        path = cache_dir / "minute" / f"{day.isoformat()}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return []
    for root in MINUTE_SOURCES:
        path = root / "minute" / f"{day.isoformat()}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return []


def minute_coverage(days: list[date]) -> tuple[int, list[date]]:
    missing: list[date] = []
    for day in days:
        if not load_minute_bars(day):
            missing.append(day)
    return len(days) - len(missing), missing


def best_minute_root(days: list[date]) -> Path:
    """Pick research root with the most minute files for the given days."""
    scores = {root: sum(1 for d in days if (root / "minute" / f"{d.isoformat()}.json").is_file()) for root in MINUTE_SOURCES}
    return max(scores, key=lambda r: scores[r])