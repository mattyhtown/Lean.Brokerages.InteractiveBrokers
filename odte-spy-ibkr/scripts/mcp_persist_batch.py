#!/usr/bin/env python3
"""Read MCP JSON payloads from stdin lines: DAY<TAB>json and persist full dumps."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ok = err = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        day, _, payload = line.partition("\t")
        try:
            subprocess.run(
                [sys.executable, "scripts/persist_mcp_option_full.py", day, payload],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            ok += 1
        except subprocess.CalledProcessError as exc:
            err += 1
            print(exc.stderr or exc.stdout, file=sys.stderr)
    print(json.dumps({"ok": ok, "errors": err}))


if __name__ == "__main__":
    main()