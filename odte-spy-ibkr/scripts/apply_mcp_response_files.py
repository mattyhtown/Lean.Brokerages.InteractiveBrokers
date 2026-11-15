#!/usr/bin/env python3
"""Save MCP get_option_bars JSON files from a response directory (``{day}_{label}.json``)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_mcp_response_files.py <response_dir>")
    resp_dir = Path(sys.argv[1])
    n = 0
    for path in sorted(resp_dir.glob("*_signal.json")) + sorted(resp_dir.glob("*_close.json")):
        if path.name.endswith("_req.json") or path.name.endswith("_meta.json"):
            continue
        stem = path.stem
        if not stem.endswith("_signal") and not stem.endswith("_close"):
            continue
        if stem.endswith("_signal"):
            day, label = stem[:-7], "signal"
        else:
            day, label = stem[:-6], "close"
        payload = json.loads(path.read_text())
        subprocess.run(
            [sys.executable, "scripts/save_mcp_option_dump.py", day, label],
            input=json.dumps(payload),
            text=True,
            check=True,
            cwd=ROOT,
        )
        n += 1
        print(path.name)
    print(json.dumps({"saved": n, "dir": str(resp_dir)}))


if __name__ == "__main__":
    main()