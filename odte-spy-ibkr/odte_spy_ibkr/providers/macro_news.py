from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from zoneinfo import ZoneInfo

from odte_spy_ibkr.config import MacroBacktestMode, Settings

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta

BEAR_WORDS = ("inflation", "hike", "selloff", "recession", "downgrade", "cpi", "jobs", "powell", "yield")
BULL_WORDS = ("cut", "rally", "beats", "upgrade", "easing", "fed")

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MacroHeadline:
    published_at: datetime
    headline: str
    symbols: list[str]
    sentiment: float


def load_macro_calendar_for_year(settings: Settings, year: int) -> list[dict[str, Any]]:
    path = Path(settings.backtest_cache_dir) / "macro_calendar" / f"{year}.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("events") or [])
    return load_macro_events(settings.macro_calendar_path)


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


def _load_headline_records(settings: Settings, day: date) -> list[MacroHeadline]:
    cache = Path(settings.backtest_cache_dir) / "macro_news" / f"{day.isoformat()}.json"
    raw: list[Any] = []
    if cache.is_file():
        return _coerce_headlines(json.loads(cache.read_text(encoding="utf-8")))
    if settings.alpaca_api_key and settings.alpaca_api_secret:
        records = _fetch_alpaca_news_records(settings, day)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                [
                    {
                        "published_at": r.published_at.isoformat(),
                        "headline": r.headline,
                        "symbols": r.symbols,
                        "sentiment": r.sentiment,
                    }
                    for r in records
                ]
            ),
            encoding="utf-8",
        )
        return records
    if settings.macro_backtest_mode == MacroBacktestMode.STRICT:
        return []
    return []


def _coerce_headlines(rows: list[Any]) -> list[MacroHeadline]:
    out: list[MacroHeadline] = []
    for row in rows:
        if isinstance(row, str):
            out.append(
                MacroHeadline(
                    published_at=datetime.now(tz=ET),
                    headline=row,
                    symbols=[],
                    sentiment=score_headline(row),
                )
            )
            continue
        if not isinstance(row, dict):
            continue
        published = row.get("published_at") or row.get("created_at")
        if isinstance(published, str):
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        else:
            published_at = datetime.now(tz=ET)
        text = str(row.get("headline") or row.get("text") or "")
        if not text:
            continue
        symbols = [str(s) for s in (row.get("symbols") or [])]
        out.append(
            MacroHeadline(
                published_at=published_at,
                headline=text,
                symbols=symbols,
                sentiment=score_headline(text),
            )
        )
    return out


def score_headlines_before(settings: Settings, day: date, as_of: datetime) -> float:
    """No-lookahead macro headline intensity: only headlines at or before as_of."""
    records = _load_headline_records(settings, day)
    lookback = timedelta(minutes=settings.macro_headline_lookback_min)
    window_start = as_of - lookback
    visible = [h for h in records if window_start <= h.published_at <= as_of]
    if not visible and settings.macro_backtest_mode == MacroBacktestMode.STRICT:
        return 0.0
    if not visible:
        return 0.0
    return max(visible, key=lambda h: abs(h.sentiment)).sentiment


def score_headlines_for_day(settings: Settings, day: date, as_of: datetime | None = None) -> float:
    as_of = as_of or datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=16, minute=0)
    return score_headlines_before(settings, day, as_of)


def _fetch_alpaca_news_records(settings: Settings, day: date) -> list[MacroHeadline]:
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
    out: list[MacroHeadline] = []
    for item in news:
        headline = item.get("headline")
        if not headline:
            continue
        ts = item.get("created_at") or item.get("updated_at")
        published_at = (
            datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else datetime.now(tz=timezone.utc)
        )
        out.append(
            MacroHeadline(
                published_at=published_at,
                headline=str(headline),
                symbols=[str(s) for s in (item.get("symbols") or [])],
                sentiment=score_headline(str(headline)),
            )
        )
    return out


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
        score = score_headlines_for_day(self._settings, now.date(), as_of=now)
        return MarketContextDelta(
            provider_id=self.provider_id,
            macro_headline_score=score,
            macro_event_blackout=self._in_blackout(now),
            macro_risk_off=score < -0.5,
            as_of=now,
            note=f"headline_score={score:.2f}",
            raw={"blackout": self._in_blackout(now)},
        )