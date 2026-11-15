#!/usr/bin/env python3
"""Persist MCP get_option_bars outputs listed in a manifest JSON file."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESP = Path("/tmp/mcp_option_responses")


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text())
    results: list[dict] = []
    RESP.mkdir(parents=True, exist_ok=True)
    for row in manifest:
        day = row["day"]
        src = Path(row["path"])
        dest = RESP / f"{day}.json"
        shutil.copyfile(src, dest)
        try:
            out = subprocess.check_output(
                [
                    sys.executable,
                    str(ROOT / "scripts/ingest_mcp_option_response_file.py"),
                    day,
                    str(dest),
                ],
                cwd=ROOT,
                text=True,
            ).strip()
            results.append({"day": day, "ok": True, "detail": json.loads(out)})
            print(f"OK {day}")
        except subprocess.CalledProcessError as exc:
            results.append({"day": day, "ok": False, "detail": str(exc)})
            print(f"FAIL {day}", file=sys.stderr)
    print(json.dumps({"persisted": sum(1 for r in results if r["ok"]), "failed": [r["day"] for r in results if not r["ok"]]}))


if __name__ == "__main__":
    main()