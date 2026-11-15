#!/usr/bin/env python3
"""Write /tmp/pending_batch_args.jsonl from fetch_option_bar_queue_pending_batch.py output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/tmp/pending_batch_args.jsonl")
PENDING = ROOT / "scripts/fetch_option_bar_queue_pending_batch.py"


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    raw = subprocess.check_output([sys.executable, str(PENDING), str(n)], text=True)
    jobs = json.loads(raw)
    for j in jobs:
        (Path("/tmp") / f"mcp_args_{j['day']}.json").write_text(json.dumps(j["arguments"]))
    lines = [json.dumps(j["arguments"]) for j in jobs]
    OUT.write_text("\n".join(lines) + ("\n" if lines else ""))
    print(json.dumps({"days": [j["day"] for j in jobs], "jsonl": str(OUT)}))


if __name__ == "__main__":
    main()