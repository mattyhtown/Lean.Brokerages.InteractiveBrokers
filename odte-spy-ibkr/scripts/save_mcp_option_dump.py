#!/usr/bin/env python3
"""Save one Alpaca MCP get_option_bars JSON payload to the research dump directory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

DUMP_ROOT = Path("data/research/mcp_dumps/options")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: save_mcp_option_dump.py <YYYY-MM-DD> <signal|close>")
    day, label = sys.argv[1], sys.argv[2]
    payload = json.load(sys.stdin)
    DUMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = DUMP_ROOT / f"{day}_{label}.json"
    path.write_text(json.dumps(payload, indent=2))
    print(path)


if __name__ == "__main__":
    main()