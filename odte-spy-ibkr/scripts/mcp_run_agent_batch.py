#!/usr/bin/env python3
"""Print next N pending MCP jobs (day, label, arguments) for agent CallDynamicTool round-trip."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "data/research/mcp_dumps/options"
REQS = Path("/tmp/all_mcp_reqs.json")


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    offset = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    reqs = json.loads(REQS.read_text())
    pending = []
    for item in reqs:
        path = DUMP / f"{item['day']}_{item['label']}.json"
        if path.is_file():
            continue
        pending.append(item)
    chunk = pending[offset : offset + limit]
    print(json.dumps(chunk))


if __name__ == "__main__":
    main()