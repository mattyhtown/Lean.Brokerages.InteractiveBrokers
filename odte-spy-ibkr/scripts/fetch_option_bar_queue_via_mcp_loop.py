#!/usr/bin/env python3
"""
Drive option_bar_queue.jsonl fetches via MCP JSON-RPC when MCP_HTTP_URL is set.
Otherwise print the next pending job batch for agent CallDynamicTool rounds.
"""

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
LOG = ROOT / "data/research/mcp_dumps/option_bar_fetch_log.jsonl"
CLIENT = ROOT / "scripts/mcp_jsonrpc_client.py"
BATCH = int(os.environ.get("OPTION_BAR_BATCH_SIZE", "6"))


def pending() -> list[dict]:
    jobs = [json.loads(line) for line in QUEUE.read_text().splitlines() if line.strip()]
    return [j for j in jobs if not (DUMP / f"{j['day']}_full.json").is_file()]


def rpc(arguments: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CLIENT), "Alpaca-trading", "get_option_bars", json.dumps(arguments)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "mcp failed")
    return json.loads(proc.stdout)


def persist(day: str, payload: dict) -> None:
    tmp = Path(f"/tmp/mcp_option_responses/{day}.json")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, indent=2))
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/ingest_mcp_option_response_file.py"), day, str(tmp)],
        check=True,
        cwd=ROOT,
    )


def main() -> None:
    url = os.environ.get("ALPACA_MCP_HTTP_URL") or os.environ.get("MCP_HTTP_URL")
    todo = pending()
    if not url:
        chunk = todo[:BATCH]
        print(
            json.dumps(
                {
                    "mode": "agent_batch",
                    "pending_total": len(todo),
                    "jobs": [
                        {
                            "day": j["day"],
                            "arguments": {k: j[k] for k in ("symbols", "timeframe", "start", "end", "limit")},
                        }
                        for j in chunk
                    ],
                },
                indent=2,
            )
        )
        return

    ok = fail = 0
    failed_days: list[str] = []
    for job in todo:
        day = job["day"]
        args = {k: job[k] for k in ("symbols", "timeframe", "start", "end", "limit")}
        try:
            payload = rpc(args)
            persist(day, payload)
            row = {"day": day, "ok": True}
            ok += 1
            print(f"OK {day}")
        except Exception as exc:  # noqa: BLE001
            row = {"day": day, "ok": False, "error": str(exc)}
            fail += 1
            failed_days.append(day)
            print(f"FAIL {day} {exc}", file=sys.stderr)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        time.sleep(0.35)
    print(json.dumps({"fetched_ok": ok, "fetched_fail": fail, "failed_days": failed_days}))


if __name__ == "__main__":
    main()