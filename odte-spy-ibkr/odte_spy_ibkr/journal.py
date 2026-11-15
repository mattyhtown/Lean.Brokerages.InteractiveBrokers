from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class DailyPnlRow:
    day: date
    pnl: float
    source: str = "paper"


def append_daily_pnl(path: str | Path, row: DailyPnlRow) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": row.day.isoformat(), "pnl": row.pnl, "source": row.source}) + "\n")