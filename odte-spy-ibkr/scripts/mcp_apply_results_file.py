#!/usr/bin/env python3
"""Apply a JSON list [{day, label, payload}] from MCP get_option_bars to research dumps."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    path = Path(sys.argv[1])
    items = json.loads(path.read_text())
    applied = 0
    for item in items:
        payload = item["payload"]
        if "_alpaca_mcp_security" in payload:
            payload = {"data": payload.get("data", payload)}
        subprocess.run(
            [sys.executable, "scripts/save_mcp_option_dump.py", item["day"], item["label"]],
            input=json.dumps(payload),
            text=True,
            check=True,
            cwd=ROOT,
        )
        applied += 1
    print(json.dumps({"applied": applied, "source": str(path)}))


if __name__ == "__main__":
    main()