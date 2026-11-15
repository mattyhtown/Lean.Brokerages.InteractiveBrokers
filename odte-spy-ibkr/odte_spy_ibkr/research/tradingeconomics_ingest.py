from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.tradingeconomics.client import TradingEconomicsClient, normalize_te_event

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
TE_ROOT = Path("data/research/tradingeconomics")
MACRO_CACHE = Path("data/cache/macro_calendar")

TE_DOCS = "https://docs.tradingeconomics.com/economic_calendar/schema/"


def require_te_key(settings: Settings) -> str:
    key = settings.trading_economics_api_key.strip()
    if not key:
        raise RuntimeError(
            "TRADING_ECONOMICS_API_KEY is not set. Subscribe at "
            "https://tradingeconomics.com/api/pricing.aspx and export TRADING_ECONOMICS_API_KEY=..."
        )
    return key


def ingest_range(settings: Settings, *, start: date, end: date) -> dict[str, Any]:
    key = require_te_key(settings)
    client = TradingEconomicsClient(key)
    raw = client.calendar_us(start=start, end=end, values=True)
    events = [normalize_te_event(row) for row in raw]
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for evt in events:
        ra = evt.get("release_at") or ""
        if not ra:
            continue
        try:
            day = datetime.fromisoformat(ra).date()
        except ValueError:
            continue
        by_year[day.year].append(evt)
        by_day[day.isoformat()].append(evt)

    TE_ROOT.mkdir(parents=True, exist_ok=True)
    (TE_ROOT / "raw").mkdir(parents=True, exist_ok=True)
    raw_path = TE_ROOT / "raw" / f"{start.isoformat()}_{end.isoformat()}.json"
    raw_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    daily_dir = TE_ROOT / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    for day_str, evs in by_day.items():
        (daily_dir / f"{day_str}.json").write_text(
            json.dumps({"date": day_str, "events": evs}, indent=2),
            encoding="utf-8",
        )

    merged_years: list[int] = []
    for year, evs in by_year.items():
        merged = _merge_year_events(year, evs, te_root=TE_ROOT / "macro_calendar")
        _write_macro_cache(year, merged)
        merged_years.append(year)

    stats = {
        "events": len(events),
        "days_with_events": len(by_day),
        "years_written": merged_years,
        "raw_path": str(raw_path),
        "docs": TE_DOCS,
    }
    (TE_ROOT / "ingest_summary.json").write_text(json.dumps(stats, indent=2))
    return stats


def _merge_year_events(year: int, fresh: list[dict[str, Any]], *, te_root: Path) -> list[dict[str, Any]]:
    te_root.mkdir(parents=True, exist_ok=True)
    path = te_root / f"{year}.json"
    existing: list[dict[str, Any]] = []
    if path.is_file():
        try:
            existing = list(json.loads(path.read_text()).get("events") or [])
        except json.JSONDecodeError:
            existing = []
    index = {str(e.get("calendar_id") or e.get("release_at")): e for e in existing}
    for evt in fresh:
        key = str(evt.get("calendar_id") or evt.get("release_at"))
        index[key] = evt
    merged = sorted(index.values(), key=lambda e: e.get("release_at", ""))
    path.write_text(
        json.dumps({"year": year, "source": "tradingeconomics", "events": merged}, indent=2),
        encoding="utf-8",
    )
    return merged


def _write_macro_cache(year: int, events: list[dict[str, Any]]) -> None:
    """Also mirror tier-1+2 releases into backtest macro_calendar for gates/research features."""
    MACRO_CACHE.mkdir(parents=True, exist_ok=True)
    tier12 = [e for e in events if int(e.get("tier") or 3) <= 2]
    path = MACRO_CACHE / f"{year}.json"
    existing: list[dict[str, Any]] = []
    if path.is_file():
        try:
            existing = list(json.loads(path.read_text()).get("events") or [])
        except json.JSONDecodeError:
            existing = []
    # Prefer TE for overlapping release_at; keep static YAML backfill for gaps.
    index = {str(e.get("release_at")): e for e in existing if e.get("source") != "tradingeconomics"}
    for evt in tier12:
        slim = {
            "name": evt.get("name"),
            "release_at": evt.get("release_at"),
            "tier": evt.get("tier"),
            "source": "tradingeconomics",
            "actual_value": evt.get("actual_value"),
            "forecast_value": evt.get("forecast_value"),
            "surprise": evt.get("surprise"),
        }
        index[str(evt.get("release_at"))] = slim
    merged = sorted(index.values(), key=lambda e: e.get("release_at", ""))
    path.write_text(json.dumps({"year": year, "events": merged}, indent=2), encoding="utf-8")