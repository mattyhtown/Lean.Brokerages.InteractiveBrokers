from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from odte_spy_ibkr.chain.skew import ChainSkewSnapshot
from odte_spy_ibkr.config import Settings, UWBacktestMode
from odte_spy_ibkr.providers.base import FlowBias, MarketContext, VolRegime
from odte_spy_ibkr.providers.macro_news import load_macro_events, score_headlines_for_day

ET = ZoneInfo("America/New_York")


def _load_uw_cache(settings: Settings, day: date) -> dict | None:
    path = Path(settings.uw_backtest_cache_dir) / f"{day.isoformat()}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _uw_bias_from_cache(payload: dict | None, mode: UWBacktestMode) -> tuple[FlowBias, float]:
    if payload is None:
        if mode == UWBacktestMode.NEUTRAL:
            return FlowBias.NEUTRAL, 0.0
        if mode == UWBacktestMode.BLOCK:
            return FlowBias.NEUTRAL, 0.0
        return FlowBias.NEUTRAL, 0.0
    score = float(payload.get("aggregate_bias", 0.0))
    if score > 0.15:
        return FlowBias.BULLISH, min(1.0, abs(score))
    if score < -0.15:
        return FlowBias.BEARISH, min(1.0, abs(score))
    return FlowBias.NEUTRAL, 0.15


def build_backtest_context(
    settings: Settings,
    *,
    day: date,
    spot: float,
    chain_skew: ChainSkewSnapshot | None = None,
    now: datetime | None = None,
) -> MarketContext:
    now = now or datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=11, minute=0)
    uw = _load_uw_cache(settings, day)
    bias, strength = _uw_bias_from_cache(uw, settings.uw_backtest_mode)
    events = load_macro_events(settings.macro_calendar_path)
    blackout = _in_blackout(events, now, settings)
    headline_score = score_headlines_for_day(settings, day)

    return MarketContext(
        underlying=settings.underlying,
        spot=spot,
        flow_bias=bias,
        flow_strength=strength,
        vol_regime=VolRegime.NORMAL,
        macro_risk_off=headline_score < -0.6,
        net_call_bias=chain_skew.net_call_bias if chain_skew else 0.0,
        chain_skew=chain_skew,
        macro_headline_score=headline_score,
        macro_event_blackout=blackout,
        as_of=now,
    )


def _in_blackout(events: list[dict], now: datetime, settings: Settings) -> bool:
    for evt in events:
        release = datetime.fromisoformat(evt["release_at"].replace("Z", "+00:00"))
        if release.date() != now.date():
            continue
        before = timedelta(minutes=settings.macro_tier1_blackout_before_min)
        after = timedelta(minutes=settings.macro_tier1_blackout_after_min)
        if release - before <= now <= release + after:
            return True
    return False