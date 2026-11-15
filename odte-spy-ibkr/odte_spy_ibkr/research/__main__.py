from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from odte_spy_ibkr.config import load_settings
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.research.alpaca_ingest import ingest_range
from odte_spy_ibkr.research.evaluate import report_markdown, run_research


def main() -> None:
    parser = argparse.ArgumentParser(description="ODTE alpha research (real Alpaca data only)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="Pull SPY bars from Alpaca Data API into data/research/alpaca/")
    ing.add_argument("--days", type=int, default=120)
    ev = sub.add_parser("evaluate", help="Walk-forward feature IC with final holdout")
    ev.add_argument("--holdout-days", type=int, default=20)
    ev.add_argument("--report", default="")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]

    if args.cmd == "ingest":
        end = date.today()
        start = end - timedelta(days=args.days)
        paths = ingest_range(settings, start=start, end=end)
        print(json.dumps(paths, indent=2))
        return
    if args.cmd == "evaluate":
        rep = run_research(settings, holdout_days=args.holdout_days)
        md = report_markdown(rep)
        if args.report:
            Path(args.report).write_text(md, encoding="utf-8")
        print(md)
        raise SystemExit(0 if not rep.any_holdout_survivor else 0)


if __name__ == "__main__":
    main()