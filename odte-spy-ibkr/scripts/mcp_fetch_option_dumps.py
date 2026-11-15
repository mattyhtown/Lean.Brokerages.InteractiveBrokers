#!/usr/bin/env python3
"""
Fetch Alpaca option-bar dumps when ALPACA_API_KEY/SECRET are set; otherwise prints MCP args for agent.

Writes: data/research/mcp_dumps/options/{day}_signal.json and {day}_close.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from odte_spy_ibkr.config import load_settings
from odte_spy_ibkr.research.alpaca_chain_ingest import fetch_option_bars_batch, occ_symbol, strikes_around
from odte_spy_ibkr.research.alpaca_ingest import load_minute_bars
from odte_spy_ibkr.research.features import SIGNAL_TIME, _price_at

ET = ZoneInfo("America/New_York")
DUMP_ROOT = Path("data/research/mcp_dumps/options")
QUEUE_PATH = Path(os.environ.get("SPY_CHAIN_QUEUE", "/tmp/spy_chain_queue.json"))


def _save(day: date, label: str, bars: dict) -> None:
    DUMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = DUMP_ROOT / f"{day.isoformat()}_{label}.json"
    path.write_text(json.dumps({"data": {"bars": bars}}, indent=2))


def _fetch_one_day(settings, day: date, symbols: list[str]) -> None:
    signal = fetch_option_bars_batch(
        settings, symbols, day=day, window_start=SIGNAL_TIME, window_end=dt_time(11, 5)
    )
    _save(day, "signal", signal)
    time.sleep(0.15)
    close = fetch_option_bars_batch(
        settings, symbols, day=day, window_start=dt_time(15, 55), window_end=dt_time(16, 5)
    )
    _save(day, "close", close)


def main() -> None:
    queue = json.loads(QUEUE_PATH.read_text())
    settings = load_settings()
    if settings.alpaca_api_key and settings.alpaca_api_secret:
        done = 0
        for row in queue:
            day = date.fromisoformat(row["day"])
            sig_path = DUMP_ROOT / f"{day.isoformat()}_signal.json"
            if sig_path.is_file():
                continue
            syms = row["symbols"].split(",")
            _fetch_one_day(settings, day, syms)
            done += 1
            print(f"fetched {day}")
        print(json.dumps({"fetched": done, "mode": "api"}))
        return

    # No API keys on this host — emit MCP parameters for external batching.
    manifest = []
    for row in queue:
        day = row["day"]
        syms = row["symbols"]
        manifest.append(
            {
                "day": day,
                "signal": {
                    "symbols": syms,
                    "timeframe": "1Min",
                    "start": f"{day}T11:00:00-04:00",
                    "end": f"{day}T11:05:00-04:00",
                },
                "close": {
                    "symbols": syms,
                    "timeframe": "1Min",
                    "start": f"{day}T15:55:00-04:00",
                    "end": f"{day}T16:05:00-04:00",
                },
            }
        )
    out = ROOT / "data/research/mcp_dumps/mcp_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"mode": "mcp_manifest", "days": len(manifest), "path": str(out)}))


if __name__ == "__main__":
    main()