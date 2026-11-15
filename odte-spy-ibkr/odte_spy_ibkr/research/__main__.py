from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from odte_spy_ibkr.config import load_settings
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.research.evaluate import report_markdown, run_research
from odte_spy_ibkr.research.ingest import log_credential_status, resolve_ingest_sources, run_ingest


def main() -> None:
    parser = argparse.ArgumentParser(description="ODTE alpha research (real market data only)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="Pull research caches (Alpaca / UW / IBKR / ORATS)")
    ing.add_argument(
        "--source",
        action="append",
        choices=[
            "alpaca",
            "uw",
            "ibkr",
            "orats",
            "thetadata",
            "tradingeconomics",
            "quantconnect",
            "openbb",
            "all",
        ],
        default=None,
        help="Repeat for multiple sources; omit for alpaca only",
    )
    ing.add_argument("--start", type=date.fromisoformat, default=None)
    ing.add_argument("--end", type=date.fromisoformat, default=None)
    ing.add_argument("--days", type=int, default=120, help="Lookback when --start omitted")
    ev = sub.add_parser("evaluate", help="Walk-forward feature IC with final holdout")
    ev.add_argument("--holdout-days", type=int, default=None, help="Run-1 tail holdout size (ignored when --run 2)")
    ev.add_argument("--run", type=int, default=2, choices=(1, 2), help="2 = fixed Oct 1–14 holdout + cumulative Bonferroni")
    ev.add_argument("--report", default="")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    log_credential_status(settings)

    if args.cmd == "ingest":
        end = args.end or date.today()
        start = args.start or (end - timedelta(days=args.days))
        paths = run_ingest(
            settings,
            sources=resolve_ingest_sources(args.source),
            start=start,
            end=end,
        )
        print(json.dumps(paths, indent=2))
        return
    if args.cmd == "evaluate":
        rep = run_research(settings, holdout_days=args.holdout_days, run=args.run)
        md = report_markdown(rep)
        if args.report:
            Path(args.report).write_text(md, encoding="utf-8")
        print(md)
        raise SystemExit(0 if not rep.any_holdout_survivor else 0)


if __name__ == "__main__":
    main()