#!/usr/bin/env python3
"""Print up to N pending MCP get_option_bars jobs from mcp_manifest.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/research/mcp_dumps/mcp_manifest.json"
DUMP_ROOT = ROOT / "data/research/mcp_dumps/options"


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    manifest = json.loads(MANIFEST.read_text())
    jobs: list[dict] = []
    for entry in manifest:
        day = entry["day"]
        for label in ("signal", "close"):
            path = DUMP_ROOT / f"{day}_{label}.json"
            if path.is_file():
                continue
            args = dict(entry[label])
            args["limit"] = 10000
            jobs.append({"day": day, "label": label, "arguments": args})
            if len(jobs) >= limit:
                break
        if len(jobs) >= limit:
            break
    print(json.dumps(jobs))


if __name__ == "__main__":
    main()