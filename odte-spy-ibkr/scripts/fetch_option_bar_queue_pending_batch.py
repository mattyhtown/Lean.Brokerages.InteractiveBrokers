#!/usr/bin/env python3
"""Print the next pending option_bar_queue jobs as JSON for parallel get_option_bars MCP calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "data/research/mcp_dumps/option_bar_queue.jsonl"
DUMP = ROOT / "data/research/mcp_dumps/options"


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    jobs = [json.loads(line) for line in QUEUE.read_text().splitlines() if line.strip()]
    pending = [j for j in jobs if not (DUMP / f"{j['day']}_full.json").is_file()]
    batch = pending[:n]
    out = [
        {
            "day": j["day"],
            "arguments": {k: j[k] for k in ("symbols", "timeframe", "start", "end", "limit")},
        }
        for j in batch
    ]
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()