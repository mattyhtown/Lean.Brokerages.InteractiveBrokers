from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from odte_spy_ibkr.chain import OptionCandidate, today_expiry


def save_chain_json(candidates: list[OptionCandidate], *, base_dir: str | Path, ticker: str, day: date | None = None) -> Path:
    day = day or today_expiry()
    path = Path(base_dir) / ticker / f"{day.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
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
            }
        )
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path