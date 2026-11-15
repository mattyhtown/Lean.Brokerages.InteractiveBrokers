#!/usr/bin/env python3
"""Emit one get_option_bars job per session with minute cache (full RTH window)."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from odte_spy_ibkr.research.alpaca_chain_ingest import occ_symbol, strikes_around
from odte_spy_ibkr.research.alpaca_ingest import load_minute_bars
from odte_spy_ibkr.research.features import SIGNAL_TIME, _price_at

ET = ZoneInfo("America/New_York")
OUT = ROOT / "data/research/mcp_dumps/option_bar_queue.jsonl"
CHAIN_ROOT = ROOT / "data/research/chains/SPY"


def main() -> None:
    minute_dir = ROOT / "data/research/alpaca/minute"
    jobs: list[dict] = []
    for path in sorted(minute_dir.glob("*.json")):
        day = date.fromisoformat(path.stem)
        if (CHAIN_ROOT / f"{day.isoformat()}.json").is_file():
            continue
        minute = load_minute_bars(day)
        spot = _price_at(minute, SIGNAL_TIME) if minute else None
        if not spot:
            continue
        syms = [
            occ_symbol("SPY", day, float(strike), right)
            for strike in strikes_around(spot)
            for right in ("P", "C")
        ]
        start = datetime.combine(day, time(9, 30), tzinfo=ET).isoformat()
        end = datetime.combine(day, time(16, 5), tzinfo=ET).isoformat()
        jobs.append(
            {
                "day": day.isoformat(),
                "symbols": ",".join(syms),
                "timeframe": "1Min",
                "start": start,
                "end": end,
                "limit": 10000,
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(j) for j in jobs) + ("\n" if jobs else ""))
    print(json.dumps({"pending": len(jobs), "queue": str(OUT)}))


if __name__ == "__main__":
    main()