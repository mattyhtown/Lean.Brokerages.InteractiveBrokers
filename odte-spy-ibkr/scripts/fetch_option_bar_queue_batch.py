#!/usr/bin/env python3
"""Fetch one option_bar_queue batch via MCP JSON-RPC when URL is set; else print jobs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "data/research/mcp_dumps/option_bar_queue.jsonl"
DUMP = ROOT / "data/research/mcp_dumps/options"
BATCH_ROOT = Path(os.environ.get("OPTION_BAR_BATCH_ROOT", "/tmp/option_bar_batches"))
MANIFEST_OUT = Path("/tmp/mcp_option_batch_manifest.json")
CLIENT = ROOT / "scripts/mcp_jsonrpc_client.py"


def _pending_jobs() -> list[dict]:
    jobs = [json.loads(line) for line in QUEUE.read_text().splitlines() if line.strip()]
    out: list[dict] = []
    for job in jobs:
        if (DUMP / f"{job['day']}_full.json").is_file():
            continue
        out.append(job)
    return out


def _rpc(arguments: dict) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(CLIENT),
            "Alpaca-trading",
            "get_option_bars",
            json.dumps(arguments),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "mcp call failed")
    return json.loads(proc.stdout)


def main() -> None:
    batch_idx = int(sys.argv[1])
    batch_path = BATCH_ROOT / f"batch_{batch_idx:03d}.json"
    if not batch_path.is_file():
        pending = _pending_jobs()
        batch_size = int(os.environ.get("OPTION_BAR_BATCH_SIZE", "6"))
        batch = pending[:batch_size]
        batch_path.parent.mkdir(parents=True, exist_ok=True)
        batch_path.write_text(json.dumps(batch, indent=2))

    jobs = json.loads(batch_path.read_text())
    url = os.environ.get("ALPACA_MCP_HTTP_URL") or os.environ.get("MCP_HTTP_URL")
    if not url:
        print(json.dumps({"mode": "print_only", "jobs": jobs}, indent=2))
        return

    manifest: list[dict] = []
    for job in jobs:
        day = job["day"]
        args = {k: job[k] for k in ("symbols", "timeframe", "start", "end", "limit")}
        try:
            payload = _rpc(args)
            tmp = Path(f"/tmp/mcp_option_responses/{day}.json")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, indent=2))
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/ingest_mcp_option_response_file.py"),
                    day,
                    str(tmp),
                ],
                check=True,
                cwd=ROOT,
            )
            manifest.append({"day": day, "path": str(tmp), "ok": True})
            print(f"OK {day}")
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            manifest.append({"day": day, "ok": False, "error": str(exc)})
            print(f"FAIL {day} {exc}", file=sys.stderr)
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"fetched": sum(1 for m in manifest if m.get("ok")), "failed": [m["day"] for m in manifest if not m.get("ok")]}))


if __name__ == "__main__":
    main()