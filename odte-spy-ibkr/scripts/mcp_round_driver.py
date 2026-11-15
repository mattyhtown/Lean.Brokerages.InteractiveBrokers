#!/usr/bin/env python3
"""Prepare MCP get_option_bars round + persist manifest; log outcomes."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "scripts/fetch_option_bar_queue_status.py"
PENDING = ROOT / "scripts/fetch_option_bar_queue_pending_batch.py"
PERSIST = ROOT / "scripts/persist_mcp_manifest.py"
LOG = ROOT / "data/research/mcp_dumps/option_bar_fetch_log.jsonl"
MANIFEST = Path("/tmp/mcp_round_manifest.json")
PENDING_OUT = Path("/tmp/pending_batch.json")
ARGS_DIR = Path("/tmp")


def log_rows(rows: list[dict]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def main() -> None:
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    status = json.loads(subprocess.check_output([sys.executable, str(STATUS)], text=True))
    if not status.get("pending_dumps"):
        print(json.dumps({"done": True, **status}))
        return

    subprocess.check_call([sys.executable, str(PENDING), str(batch)], stdout=open(PENDING_OUT, "w"))
    jobs = json.loads(PENDING_OUT.read_text())
    for j in jobs:
        (ARGS_DIR / f"mcp_args_{j['day']}.json").write_text(json.dumps(j["arguments"]))

    MANIFEST.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "mode": "await_manifest",
                "manifest_path": str(MANIFEST),
                "days": [j["day"] for j in jobs],
                "pending_total": len(status["pending_dumps"]),
            }
        )
    )

    if "--wait" not in sys.argv:
        return

    deadline = time.time() + float(sys.argv[sys.argv.index("--wait") + 1] if len(sys.argv) > sys.argv.index("--wait") + 1 else 3600)
    while not MANIFEST.is_file() and time.time() < deadline:
        time.sleep(0.5)
    if not MANIFEST.is_file():
        raise SystemExit("timeout waiting for manifest")

    out = subprocess.check_output([sys.executable, str(PERSIST), str(MANIFEST)], text=True)
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("OK "):
            rows.append({"day": line[3:], "ok": True})
        elif line.startswith("FAIL "):
            parts = line.split(maxsplit=2)
            rows.append({"day": parts[1], "ok": False, "error": parts[2] if len(parts) > 2 else ""})
    log_rows(rows)
    stat = json.loads(subprocess.check_output([sys.executable, str(STATUS)], text=True))
    print(json.dumps({"persisted_round": len(rows), "status": stat}))


if __name__ == "__main__":
    main()