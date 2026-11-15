#!/usr/bin/env python3
"""Merge Alpaca MCP get_stock_bars JSON into data/research/alpaca/minute/{date}.json"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "research" / "alpaca"


def absorb(payload: dict) -> dict[str, list]:
    layer = payload.get("data", payload)
    bars = layer.get("bars", {}) if isinstance(layer, dict) else {}
    spy = bars.get("SPY", bars) if isinstance(bars, dict) else bars
    by_day: dict[str, list] = defaultdict(list)
    for bar in spy:
        by_day[bar["t"][:10]].append(bar)
    return by_day


def main() -> None:
    by_day: dict[str, list] = defaultdict(list)
    for path in sys.argv[1:]:
        by_day.update({k: v for k, v in absorb(json.loads(Path(path).read_text())).items()})
    out = ROOT / "minute"
    out.mkdir(parents=True, exist_ok=True)
    for day, rows in sorted(by_day.items()):
        (out / f"{day}.json").write_text(json.dumps(rows), encoding="utf-8")
    print(f"merged {len(by_day)} day files into {out}")


if __name__ == "__main__":
    main()