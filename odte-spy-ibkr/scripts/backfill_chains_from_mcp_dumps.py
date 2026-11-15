#!/usr/bin/env python3
"""Build unified chain cache from per-day Alpaca MCP get_option_bars JSON dumps."""

from __future__ import annotations

import json
import sys
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from odte_spy_ibkr.research.alpaca_chain_ingest import bars_to_chain_rows, occ_symbol, strikes_around
from odte_spy_ibkr.research.alpaca_ingest import load_minute_bars
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.features import SIGNAL_TIME, _price_at

DUMP_ROOT = Path("data/research/mcp_dumps/options")


def _bars(payload: dict) -> dict[str, list]:
    layer = payload.get("data", payload)
    return layer.get("bars") or {}


def _ingest_day(day: date) -> tuple[bool, str]:
    full_path = DUMP_ROOT / f"{day.isoformat()}_full.json"
    signal_path = DUMP_ROOT / f"{day.isoformat()}_signal.json"
    close_path = DUMP_ROOT / f"{day.isoformat()}_close.json"
    if full_path.is_file():
        bars = _bars(json.loads(full_path.read_text()))
    elif signal_path.is_file():
        bars = _bars(json.loads(signal_path.read_text()))
    else:
        return False, "missing option dump"
    minute = load_minute_bars(day)
    spot = _price_at(minute, SIGNAL_TIME) if minute else None
    if not spot:
        return False, "no spot"
    symbol_map: dict[str, tuple[float, str]] = {}
    for strike in strikes_around(spot):
        for right in ("P", "C"):
            sym = occ_symbol("SPY", day, float(strike), right)
            symbol_map[sym] = (float(strike), right)
    if full_path.is_file():
        signal_rows = bars_to_chain_rows(
            symbol_map, bars, day=day, window_start=time(11, 0), window_end=time(11, 5)
        )
        close_rows = bars_to_chain_rows(
            symbol_map, bars, day=day, window_start=time(15, 55), window_end=time(16, 5)
        )
    else:
        signal_rows = bars_to_chain_rows(symbol_map, bars, day=day)
        close_rows = (
            bars_to_chain_rows(symbol_map, _bars(json.loads(close_path.read_text())), day=day)
            if close_path.is_file()
            else []
        )
    if not signal_rows:
        return False, "empty signal rows"
    save_chain_snapshot(signal_rows, day, snapshot="signal", source="alpaca_option_bars")
    if close_rows:
        save_chain_snapshot(close_rows, day, snapshot="close", source="alpaca_option_bars")
    return True, f"{len(signal_rows)} legs"


def main() -> None:
    ok = empty = 0
    days: set[date] = set()
    for path in DUMP_ROOT.glob("*_full.json"):
        days.add(date.fromisoformat(path.name.replace("_full.json", "")))
    for path in DUMP_ROOT.glob("*_signal.json"):
        days.add(date.fromisoformat(path.name.replace("_signal.json", "")))
    for day in sorted(days):
        success, msg = _ingest_day(day)
        if success:
            ok += 1
            print(f"OK {day} {msg}")
        else:
            empty += 1
            print(f"SKIP {day} {msg}", file=sys.stderr)
    summary = {"days_chain": ok, "days_skipped": empty, "dump_dir": str(DUMP_ROOT)}
    out = ROOT / "data/research/chains/SPY/_ingest_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()