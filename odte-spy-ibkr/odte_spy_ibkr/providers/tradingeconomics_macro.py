from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta
from odte_spy_ibkr.providers.macro_news import load_macro_calendar_for_year

ET = ZoneInfo("America/New_York")


class TradingEconomicsMacroProvider(BaseProvider):
    """US economic calendar actual / forecast / surprise (Trading Economics)."""

    provider_id = "tradingeconomics"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled="tradingeconomics" in settings.providers_enabled_set)
        self._settings = settings

    def is_configured(self) -> bool:
        return bool(self._settings.trading_economics_api_key.strip())

    def _in_blackout(self, now: datetime) -> bool:
        for evt in load_macro_calendar_for_year(self._settings, now.year):
            if int(evt.get("tier") or 3) > 1:
                continue
            release = datetime.fromisoformat(str(evt["release_at"]).replace("Z", "+00:00"))
            if release.tzinfo is None:
                release = release.replace(tzinfo=ET)
            if release.date() != now.date():
                continue
            before = timedelta(minutes=self._settings.macro_tier1_blackout_before_min)
            after = timedelta(minutes=self._settings.macro_tier1_blackout_after_min)
            if release - before <= now <= release + after:
                return True
        return False

    def _surprise_today(self, day, *, as_of: datetime) -> float | None:
        path = Path("data/research/tradingeconomics/daily") / f"{day.isoformat()}.json"
        if not path.is_file():
            return None
        try:
            events = json.loads(path.read_text()).get("events") or []
        except json.JSONDecodeError:
            return None
        best: float | None = None
        for evt in events:
            if int(evt.get("importance") or evt.get("tier") or 0) < 3:
                continue
            surprise = evt.get("surprise")
            if surprise is None:
                continue
            ra = evt.get("release_at")
            if not ra:
                continue
            release = datetime.fromisoformat(str(ra).replace("Z", "+00:00"))
            if release.tzinfo is None:
                release = release.replace(tzinfo=ET)
            if release > as_of:
                continue
            val = float(surprise)
            if best is None or abs(val) > abs(best):
                best = val
        return best

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        now = ctx.as_of if ctx and ctx.as_of else datetime.now(tz=timezone.utc)
        now_et = now.astimezone(ET)
        surprise = self._surprise_today(now_et.date(), as_of=now_et)
        blackout = self._in_blackout(now_et)
        return MarketContextDelta(
            provider_id=self.provider_id,
            macro_event_blackout=blackout,
            macro_risk_off=surprise is not None and surprise < 0,
            as_of=now_et,
            note=f"te_surprise={surprise}" if surprise is not None else "te_calendar",
            raw={"surprise": surprise, "blackout": blackout},
        )