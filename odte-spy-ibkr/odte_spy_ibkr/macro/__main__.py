from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from odte_spy_ibkr.config import load_settings

ET = ZoneInfo("America/New_York")


def _read_source(path: Path) -> list[dict[str, Any]]:
    """Events from YAML or JSON (`{"events": [...]}` or a bare list) or CSV (name,release_at[,tier])."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            return [dict(row) for row in csv.DictReader(f)]
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if isinstance(data, dict):
        data = data.get("events")
    return [dict(row) for row in (data or [])]


def normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Timezone-aware `release_at` (naive times are US/Eastern) and an integer `tier` (default 1)."""
    release_raw = raw.get("release_at")
    if not release_raw:
        return None
    release = (
        release_raw
        if isinstance(release_raw, datetime)
        else datetime.fromisoformat(str(release_raw).replace("Z", "+00:00"))
    )
    if release.tzinfo is None:
        release = release.replace(tzinfo=ET)
    event = {k: v for k, v in raw.items() if v not in (None, "")}
    event["name"] = str(raw.get("name") or "").strip() or "unnamed"
    event["release_at"] = release.isoformat()
    event["tier"] = int(raw.get("tier") or 1)
    return event


def backfill_calendar(*, source_yaml: str | Path, year: int, cache_dir: str | Path) -> Path:
    """Merge `year`'s events from a source file into `{cache_dir}/macro_calendar/{year}.json`.

    Existing entries are kept; an event already present (same name and release instant) is
    replaced by the source version.
    """
    out = Path(cache_dir) / "macro_calendar" / f"{year}.json"
    existing: dict[str, Any] = {}
    if out.is_file():
        loaded = json.loads(out.read_text(encoding="utf-8"))
        existing = loaded if isinstance(loaded, dict) else {"events": loaded}

    merged: dict[tuple[str, datetime], dict[str, Any]] = {}
    for raw in [*(existing.get("events") or []), *_read_source(Path(source_yaml))]:
        event = normalize_event(raw)
        if event is None:
            continue
        release = datetime.fromisoformat(event["release_at"])
        if release.astimezone(ET).year != year:
            continue
        merged[(event["name"], release)] = event

    payload = {**existing, "year": year, "events": [merged[key] for key in sorted(merged, key=lambda k: k[1])]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ODTE macro utilities")
    parser.add_argument("command", choices=["backfill"])
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--source",
        default=None,
        help="Calendar file to import: .yaml, .json or .csv (default: MACRO_CALENDAR_PATH)",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    path = backfill_calendar(
        source_yaml=args.source or settings.macro_calendar_path,
        year=args.year,
        cache_dir=settings.backtest_cache_dir,
    )
    events = json.loads(path.read_text(encoding="utf-8"))["events"]
    print(f"{path}: {len(events)} event(s)")


if __name__ == "__main__":
    main()
