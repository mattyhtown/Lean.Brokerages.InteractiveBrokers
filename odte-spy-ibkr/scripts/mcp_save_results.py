#!/usr/bin/env python3
"""Save MCP get_option_bars results: JSON list of {day, label, payload} on stdin or file."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) > 1:
        items = json.loads(Path(sys.argv[1]).read_text())
    else:
        items = json.loads(sys.stdin.read())
    saved = 0
    for item in items:
        day, label, payload = item["day"], item["label"], item["payload"]
        if "_alpaca_mcp_security" in payload and "data" in payload:
            payload = {"data": payload["data"]}
        subprocess.run(
            [sys.executable, "scripts/save_mcp_option_dump.py", day, label],
            input=json.dumps(payload),
            text=True,
            check=True,
            cwd=ROOT,
        )
        saved += 1
    print(json.dumps({"saved": saved}))


if __name__ == "__main__":
    main()