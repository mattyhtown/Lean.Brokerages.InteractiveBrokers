#!/usr/bin/env python3
"""Ingest all MCP response JSON files from a directory (one file per day ISO name)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data/research/mcp_dumps/option_bar_fetch_log.jsonl"


def main() -> None:
    resp_dir = Path(sys.argv[1])
    results: list[dict] = []
    for path in sorted(resp_dir.glob("*.json")):
        day = path.stem
        try:
            out = subprocess.check_output(
                [
                    sys.executable,
                    str(ROOT / "scripts/ingest_mcp_option_response_file.py"),
                    day,
                    str(path),
                ],
                cwd=ROOT,
                text=True,
            ).strip()
            row = {"day": day, "ok": True, "detail": json.loads(out)}
            print(json.dumps(row))
            results.append(row)
        except subprocess.CalledProcessError as exc:
            row = {"day": day, "ok": False, "detail": str(exc)}
            print(json.dumps(row), file=sys.stderr)
            results.append(row)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        for row in results:
            fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()