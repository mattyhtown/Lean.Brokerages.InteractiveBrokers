#!/usr/bin/env python3
"""Split Alpaca MCP get_stock_bars JSON dumps into data/research/alpaca/minute/{date}.json"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "research" / "alpaca"


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    layer = payload.get("data", payload)
    if isinstance(layer, dict) and "bars" in layer:
        bars = layer["bars"]
    elif isinstance(layer, dict) and "error" in layer:
        raise SystemExit(f"Alpaca error in dump: {layer['error']}")
    else:
        bars = payload.get("bars", {})
    spy = bars.get("SPY", bars) if isinstance(bars, dict) else bars
    by_day: dict[str, list] = defaultdict(list)
    for bar in spy:
        day = bar["t"][:10]
        by_day[day].append(bar)
    out = ROOT / "minute"
    out.mkdir(parents=True, exist_ok=True)
    for day, rows in sorted(by_day.items()):
        (out / f"{day}.json").write_text(json.dumps(rows), encoding="utf-8")
    print(f"wrote {len(by_day)} minute files to {out}")


if __name__ == "__main__":
    main()