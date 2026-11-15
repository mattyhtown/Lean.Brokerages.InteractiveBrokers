from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.alpaca import AlpacaDataError, alpaca_configured, alpaca_paged_get
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta

logger = logging.getLogger(__name__)

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


# Known only once the number prints; scheduled fields (name, tier, release_at, forecast,
# previous) are public beforehand.
EVENT_OUTCOME_FIELDS = ("actual", "surprise", "revised", "outcome")


def event_release_time(evt: dict[str, Any]) -> datetime | None:
    raw = evt.get("release_at")
    if not raw:
        return None
    try:
        release = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return release if release.tzinfo else release.replace(tzinfo=ET)


def tier1_blackout_active(events: list[dict[str, Any]], now: datetime, settings: Settings) -> bool:
    """True inside [release - before, release + after] of any tier-1 event."""
    before = timedelta(minutes=settings.macro_tier1_blackout_before_min)
    after = timedelta(minutes=settings.macro_tier1_blackout_after_min)
    for evt in events:
        release = event_release_time(evt)
        if release is None or int(evt.get("tier") or 1) != 1:
            continue
        if release - before <= now <= release + after:
            return True
    return False


def visible_macro_events(events: list[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    """Calendar as it was knowable at `as_of`: outcomes of unreleased events are removed."""
    out: list[dict[str, Any]] = []
    for evt in events:
        release = event_release_time(evt)
        if release is not None and release > as_of:
            evt = {k: v for k, v in evt.items() if k not in EVENT_OUTCOME_FIELDS}
        out.append(evt)
    return out


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


def _headline_cache_path(settings: Settings, day: date) -> Path:
    return Path(settings.backtest_cache_dir) / "macro_news" / f"{day.isoformat()}.json"


def _cache_needs_refresh(settings: Settings, payload: Any, day: date, now: datetime) -> bool:
    """A fetch made before the day ended (or more than a refresh interval ago, intraday) is partial."""
    if not isinstance(payload, dict) or not payload.get("fetched_at"):
        return False
    fetched_at = datetime.fromisoformat(str(payload["fetched_at"]).replace("Z", "+00:00"))
    day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=ET)
    return fetched_at < min(now, day_end) - timedelta(seconds=settings.macro_headline_refresh_sec)


def load_headline_records(
    settings: Settings,
    day: date,
    *,
    now: datetime | None = None,
) -> list[MacroHeadline] | None:
    """Headlines for `day` from cache, else Alpaca news; None when neither is available."""
    now = now or datetime.now(tz=timezone.utc)
    cache = _headline_cache_path(settings, day)
    payload = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else None
    cached = None
    if payload is not None:
        cached = _coerce_headlines(payload.get("headlines") or [] if isinstance(payload, dict) else payload)
        if not _cache_needs_refresh(settings, payload, day, now):
            return cached
    if alpaca_configured(settings):
        try:
            records = _fetch_alpaca_news_records(settings, day)
        except AlpacaDataError as exc:
            logger.warning("Alpaca news unavailable for %s: %s", day, exc)
            return cached
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {
                    "fetched_at": now.isoformat(),
                    "headlines": [
                        {
                            "published_at": r.published_at.isoformat(),
                            "headline": r.headline,
                            "symbols": r.symbols,
                            "sentiment": r.sentiment,
                        }
                        for r in records
                    ],
                }
            ),
            encoding="utf-8",
        )
        return records
    return cached


def _coerce_headlines(rows: list[Any]) -> list[MacroHeadline]:
    out: list[MacroHeadline] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        published = row.get("published_at") or row.get("created_at")
        if not isinstance(published, str):
            # Without a publication time the headline cannot be placed on the replay clock.
            continue
        published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
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


def score_visible_headlines(
    records: list[MacroHeadline],
    as_of: datetime,
    *,
    lookback_min: int,
) -> float:
    """Strongest headline published within the lookback window ending at `as_of`."""
    window_start = as_of - timedelta(minutes=lookback_min)
    visible = [h for h in records if window_start <= h.published_at <= as_of]
    if not visible:
        return 0.0
    return max(visible, key=lambda h: abs(h.sentiment)).sentiment


def score_headlines_before(settings: Settings, day: date, as_of: datetime) -> float:
    """No-lookahead macro headline intensity: only headlines at or before as_of."""
    records = load_headline_records(settings, day) or []
    return score_visible_headlines(records, as_of, lookback_min=settings.macro_headline_lookback_min)


def score_headlines_for_day(settings: Settings, day: date, as_of: datetime | None = None) -> float:
    as_of = as_of or datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=16, minute=0)
    return score_headlines_before(settings, day, as_of)


def _fetch_alpaca_news_records(settings: Settings, day: date) -> list[MacroHeadline]:
    """GET /v1beta1/news for the ET calendar day, all pages, oldest first."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=ET)

    def fmt(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "symbols": settings.underlying,
        "start": fmt(start),
        "end": fmt(start + timedelta(days=1)),
        "limit": 50,
        "sort": "asc",
    }
    out: list[MacroHeadline] = []
    for page in alpaca_paged_get(settings, "/v1beta1/news", params):
        for item in page.get("news") or []:
            headline = item.get("headline")
            ts = item.get("created_at")
            if not headline or not ts:
                continue
            out.append(
                MacroHeadline(
                    published_at=datetime.fromisoformat(str(ts).replace("Z", "+00:00")),
                    headline=str(headline),
                    symbols=[str(sym) for sym in (item.get("symbols") or [])],
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
            if release.date() != now.date() or int(evt.get("tier") or 1) != 1:
                continue
            before = timedelta(minutes=self._settings.macro_tier1_blackout_before_min)
            after = timedelta(minutes=self._settings.macro_tier1_blackout_after_min)
            if release - before <= now <= release + after:
                return True
        return False

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        now = ctx.as_of if ctx and ctx.as_of else datetime.now(tz=timezone.utc)
        score = score_headlines_for_day(self._settings, now.astimezone(ET).date(), as_of=now)
        blackout = self._in_blackout(now)
        return MarketContextDelta(
            provider_id=self.provider_id,
            macro_headline_score=score,
            macro_event_blackout=blackout,
            macro_risk_off=score < -0.5,
            as_of=now,
            note=f"headline_score={score:.2f}",
            raw={"blackout": blackout},
        )
