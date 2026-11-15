#!/usr/bin/env python3
"""Build /tmp/mcp_round_manifest.json from days + agent-tools output paths (one pair per two args)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path("/tmp/mcp_round_manifest.json")


def main() -> None:
    if len(sys.argv) < 2 or len(sys.argv[1:]) % 2:
        raise SystemExit("usage: build_round_manifest.py DAY PATH [DAY PATH ...]")
    rows = []
    args = sys.argv[1:]
    for i in range(0, len(args), 2):
        rows.append({"day": args[i], "path": args[i + 1]})
    OUT.write_text(json.dumps(rows, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()