#!/usr/bin/env python3
"""Report option bar queue fetch/backfill status as JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "data/research/mcp_dumps/option_bar_queue.jsonl"
DUMP = ROOT / "data/research/mcp_dumps/options"
CHAINS = ROOT / "data/research/chains/SPY"


def main() -> None:
    days = [json.loads(line)["day"] for line in QUEUE.read_text().splitlines() if line.strip()]
    full = [d for d in days if (DUMP / f"{d}_full.json").is_file()]
    pending = [d for d in days if d not in full]
    chains = sorted(p.name for p in CHAINS.glob("*.json")) if CHAINS.is_dir() else []
    print(
        json.dumps(
            {
                "queue_days": len(days),
                "full_dumps": len(full),
                "pending_dumps": pending,
                "chain_files": len(chains),
            }
        )
    )


if __name__ == "__main__":
    main()