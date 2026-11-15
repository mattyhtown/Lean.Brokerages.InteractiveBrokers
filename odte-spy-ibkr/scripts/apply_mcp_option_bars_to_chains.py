#!/usr/bin/env python3
"""Convert Alpaca MCP get_option_bars JSON dumps into unified chain cache files."""

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


def _load_bars(path: Path) -> dict[str, list]:
    payload = json.loads(path.read_text())
    layer = payload.get("data", payload)
    return layer.get("bars") or {}


def main() -> None:
    day = date.fromisoformat(sys.argv[1])
    signal_dump = Path(sys.argv[2])
    close_dump = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    minute = load_minute_bars(day)
    spot = _price_at(minute, SIGNAL_TIME) or _price_at(minute, time(9, 35))
    if not spot:
        raise SystemExit(f"No spot for {day}")
    symbol_map = {}
    for strike in strikes_around(spot):
        for right in ("P", "C"):
            sym = occ_symbol("SPY", day, float(strike), right)
            symbol_map[sym] = (float(strike), right)
    signal_rows = bars_to_chain_rows(symbol_map, _load_bars(signal_dump), day=day)
    if not signal_rows:
        raise SystemExit(f"No signal chain rows for {day}")
    save_chain_snapshot(signal_rows, day, snapshot="signal", source="alpaca_option_bars")
    if close_dump and close_dump.is_file():
        close_rows = bars_to_chain_rows(symbol_map, _load_bars(close_dump), day=day)
        if close_rows:
            save_chain_snapshot(close_rows, day, snapshot="close", source="alpaca_option_bars")
    print(f"wrote chain for {day} ({len(signal_rows)} legs)")


if __name__ == "__main__":
    main()