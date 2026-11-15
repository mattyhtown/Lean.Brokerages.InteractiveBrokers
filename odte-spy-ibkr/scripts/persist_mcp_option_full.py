#!/usr/bin/env python3
"""Save MCP get_option_bars payload as {day}_full.json for chain backfill."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "data/research/mcp_dumps/options"


def main() -> None:
    day = date.fromisoformat(sys.argv[1])
    raw = sys.argv[2]
    if raw.startswith("@"):
        payload = json.loads(Path(raw[1:]).read_text())
    else:
        payload = json.loads(raw)
    payload.pop("_alpaca_mcp_security", None)
    if "data" in payload and "bars" in payload.get("data", {}):
        wrapped = payload
    else:
        wrapped = {"data": payload if "bars" in payload else payload}
    DUMP.mkdir(parents=True, exist_ok=True)
    out = DUMP / f"{day.isoformat()}_full.json"
    out.write_text(json.dumps(wrapped, indent=2))
    bars = wrapped.get("data", {}).get("bars") or {}
    print(json.dumps({"day": day.isoformat(), "symbols_with_bars": len(bars), "path": str(out)}))


if __name__ == "__main__":
    main()