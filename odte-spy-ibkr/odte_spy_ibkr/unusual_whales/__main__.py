from __future__ import annotations

import argparse

from odte_spy_ibkr.config import load_settings
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.unusual_whales.collector import collect_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Append Unusual Whales session snapshot to backtest cache JSONL")
    parser.add_argument("--once", action="store_true", help="Poll UW once and append to today's session file")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    if not args.once:
        parser.error("Use --once to collect a single snapshot")
    collect_once(settings)
    raise SystemExit(0)


if __name__ == "__main__":
    main()