from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta

BEAR_WORDS = ("inflation", "hike", "selloff", "recession", "downgrade")
BULL_WORDS = ("cut", "rally", "beats", "upgrade", "easing")


def load_macro_events(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(data.get("events") or [])


def score_headline(text: str) -> float:
    lower = text.lower()
    score = 0.0
    for w in BEAR_WORDS:
        if w in lower:
            score -= 0.25
    for w in BULL_WORDS:
        if w in lower:
            score += 0.25
    return max(-1.0, min(1.0, score))


def score_headlines_for_day(settings: Settings, day: date) -> float:
    cache = Path(settings.backtest_cache_dir) / "macro_news" / f"{day.isoformat()}.json"
    headlines: list[str] = []
    if cache.is_file():
        headlines = json.loads(cache.read_text(encoding="utf-8"))
    elif settings.alpaca_api_key and settings.alpaca_api_secret:
        headlines = _fetch_alpaca_news(settings, day)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(headlines), encoding="utf-8")
    if not headlines:
        return 0.0
    return max(score_headline(h) for h in headlines)


def _fetch_alpaca_news(settings: Settings, day: date) -> list[str]:
    url = "https://data.alpaca.markets/v1beta1/news"
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    params = {
        "symbols": settings.underlying,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": 50,
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return []
    news = data.get("news") or data.get("data") or []
    return [str(item.get("headline", "")) for item in news if item.get("headline")]


class MacroNewsProvider(BaseProvider):
    provider_id = "macro_news"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled="macro_news" in settings.providers_enabled_set)
        self._settings = settings

    def is_configured(self) -> bool:
        return self._settings.macro_enabled

    def _in_blackout(self, now: datetime) -> bool:
        for evt in load_macro_events(self._settings.macro_calendar_path):
            release = datetime.fromisoformat(str(evt["release_at"]).replace("Z", "+00:00"))
            if release.date() != now.date():
                continue
            before = timedelta(minutes=self._settings.macro_tier1_blackout_before_min)
            after = timedelta(minutes=self._settings.macro_tier1_blackout_after_min)
            if release - before <= now <= release + after:
                return True
        return False

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        now = ctx.as_of if ctx and ctx.as_of else datetime.now(tz=timezone.utc)
        score = score_headlines_for_day(self._settings, now.date())
        return MarketContextDelta(
            provider_id=self.provider_id,
            macro_headline_score=score,
            macro_event_blackout=self._in_blackout(now),
            macro_risk_off=score < -0.5,
            as_of=now,
            note=f"headline_score={score:.2f}",
            raw={"blackout": self._in_blackout(now)},
        )