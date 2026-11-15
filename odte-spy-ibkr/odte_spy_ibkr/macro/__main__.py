from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from odte_spy_ibkr.config import load_settings


def backfill_calendar(*, source_yaml: str, year: int, cache_dir: str) -> Path:
    data = yaml.safe_load(Path(source_yaml).read_text(encoding="utf-8")) or {}
    events = [e for e in (data.get("events") or []) if str(e.get("release_at", "")).startswith(str(year))]
    out_dir = Path(cache_dir) / "macro_calendar"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{year}.json"
    out.write_text(json.dumps({"year": year, "events": events}, indent=2), encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="ODTE macro utilities")
    parser.add_argument("command", choices=["backfill"])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    settings = load_settings()
    if args.command == "backfill":
        path = backfill_calendar(
            source_yaml=settings.macro_calendar_path,
            year=args.year,
            cache_dir=settings.backtest_cache_dir,
        )
        print(path)
    raise SystemExit(0)


if __name__ == "__main__":
    main()