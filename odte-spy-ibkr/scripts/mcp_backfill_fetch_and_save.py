#!/usr/bin/env python3
"""
Fetch option bars via Alpaca MCP JSON-RPC and save dumps.

Reads pending work from batch dirs under /tmp/mcp_batches/{batch}/*_req.json.
Writes *_resp.json next to each request, then applies save_mcp_option_dump.py.

Requires ALPACA_MCP_URL (optional) defaulting to the Cursor-injected Alpaca MCP HTTP
endpoint when run inside cloud agent with scripts/mcp_jsonrpc_client.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP_ROOT = ROOT / "data/research/mcp_dumps/options"
BATCH_ROOT = Path(os.environ.get("MCP_BATCH_ROOT", "/tmp/mcp_batches"))


def _pending(req_path: Path) -> bool:
    name = req_path.name.replace("_req.json", "")
    day, label = name.rsplit("_", 1)
    sig = DUMP_ROOT / f"{day}_signal.json"
    close = DUMP_ROOT / f"{day}_close.json"
    if sig.is_file() and close.is_file():
        return False
    target = DUMP_ROOT / f"{day}_{label}.json"
    return not target.is_file()


def _rpc(arguments: dict) -> dict:
    client = ROOT / "scripts/mcp_jsonrpc_client.py"
    proc = subprocess.run(
        [sys.executable, str(client), "Alpaca-trading", "get_option_bars", json.dumps(arguments)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "mcp call failed")
    return json.loads(proc.stdout)


def _save(day: str, label: str, payload: dict) -> None:
    subprocess.run(
        [sys.executable, "scripts/save_mcp_option_dump.py", day, label],
        input=json.dumps(payload),
        text=True,
        check=True,
        cwd=ROOT,
    )


def main() -> None:
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else -1
    batches = [batch] if batch >= 0 else sorted(int(p.name) for p in BATCH_ROOT.iterdir() if p.is_dir() and p.name.isdigit())
    fetched = skipped = errors = 0
    for bi in batches:
        req_dir = BATCH_ROOT / str(bi)
        for req_path in sorted(req_dir.glob("*_req.json")):
            if not _pending(req_path):
                skipped += 1
                continue
            name = req_path.name.replace("_req.json", "")
            day, label = name.rsplit("_", 1)
            resp_path = req_dir / f"{day}_{label}.json"
            arguments = json.loads(req_path.read_text())
            try:
                payload = _rpc(arguments)
            except Exception as exc:  # noqa: BLE001
                print(f"ERR {day} {label} {exc}", file=sys.stderr)
                errors += 1
                continue
            resp_path.write_text(json.dumps(payload, indent=2))
            _save(day, label, payload)
            fetched += 1
            print(f"OK {day} {label}")
    print(json.dumps({"fetched": fetched, "skipped": skipped, "errors": errors}))


if __name__ == "__main__":
    main()