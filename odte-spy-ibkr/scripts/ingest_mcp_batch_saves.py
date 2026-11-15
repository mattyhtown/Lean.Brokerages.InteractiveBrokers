#!/usr/bin/env python3
"""Apply batched MCP get_option_bars payloads from a JSON file to dump files."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ingest_mcp_batch_saves.py <batch.json>")
    items = json.loads(Path(sys.argv[1]).read_text())
    for item in items:
        day, label, payload = item["day"], item["label"], item["payload"]
        subprocess.run(
            [sys.executable, "scripts/save_mcp_option_dump.py", day, label],
            input=json.dumps(payload),
            text=True,
            check=True,
            cwd=ROOT,
        )


if __name__ == "__main__":
    main()