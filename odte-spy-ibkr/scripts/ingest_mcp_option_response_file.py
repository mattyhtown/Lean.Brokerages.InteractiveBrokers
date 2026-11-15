#!/usr/bin/env python3
"""Persist one get_option_bars MCP JSON file via persist_mcp_option_full."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    day, src = sys.argv[1], Path(sys.argv[2])
    if not src.is_file():
        raise SystemExit(f"missing response file: {src}")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/persist_mcp_option_full.py"),
            day,
            f"@{src}",
        ],
        check=True,
        cwd=ROOT,
    )


if __name__ == "__main__":
    main()