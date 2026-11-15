from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.macro_news import load_macro_calendar_for_year

from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def macro_tier1_surprise_before_signal(settings: Settings, day: date, *, signal_at: datetime) -> float | None:
    """
    Max |actual - forecast| (signed surprise with largest magnitude) among tier-1
    events released on `day` at or before `signal_at`. Requires Trading Economics ingest
    (`values=true`) or calendar rows with surprise fields; otherwise null.
    """
    daily = Path("data/research/tradingeconomics/daily") / f"{day.isoformat()}.json"
    events: list[dict] = []
    if daily.is_file():
        try:
            events = list(json.loads(daily.read_text()).get("events") or [])
        except json.JSONDecodeError:
            events = []
    if not events:
        events = load_macro_calendar_for_year(settings, day.year)
    best: float | None = None
    for evt in events:
        tier = int(evt.get("tier") or 3)
        importance = int(evt.get("importance") or (1 if tier == 1 else 0))
        if tier > 1 and importance < 3:
            continue
        surprise = evt.get("surprise")
        if surprise is None:
            av, fv = evt.get("actual_value"), evt.get("forecast_value")
            if av is not None and fv is not None:
                surprise = float(av) - float(fv)
        if surprise is None:
            continue
        ra = evt.get("release_at")
        if not ra:
            continue
        release = datetime.fromisoformat(str(ra).replace("Z", "+00:00"))
        if release.tzinfo is None:
            release = release.replace(tzinfo=ET)
        release = release.astimezone(ET)
        if release.date() != day or release > signal_at:
            continue
        val = float(surprise)
        if best is None or abs(val) > abs(best):
            best = val
    return best