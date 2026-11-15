from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from odte_spy_ibkr.chain.skew import ChainSkewSnapshot
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import MarketContext, VolRegime
from odte_spy_ibkr.providers.macro_news import load_macro_calendar_for_year, score_headlines_before
from odte_spy_ibkr.providers.uw_backtest import UwBacktestReplay

ET = ZoneInfo("America/New_York")


def build_backtest_context(
    settings: Settings,
    *,
    day: date,
    spot: float,
    chain_skew: ChainSkewSnapshot | None = None,
    now: datetime | None = None,
) -> MarketContext | None:
    """Returns None when UW_BACKTEST_MODE=skip and cache is missing (skip session)."""
    now = now or datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=11, minute=0)
    uw = UwBacktestReplay(settings)
    bias, strength, block, uw_reason = uw.flow_signal(day)
    if block and uw_reason.endswith("skip"):
        return None

    events = load_macro_calendar_for_year(settings, day.year)
    blackout = _in_blackout(events, now, settings)
    headline_score = score_headlines_before(settings, day, now)

    return MarketContext(
        underlying=settings.underlying,
        spot=spot,
        flow_bias=bias,
        flow_strength=0.0 if block else strength,
        vol_regime=VolRegime.NORMAL,
        macro_risk_off=headline_score < -0.6,
        net_call_bias=chain_skew.net_call_bias if chain_skew else 0.0,
        chain_skew=chain_skew,
        macro_headline_score=headline_score,
        macro_event_blackout=blackout or block,
        as_of=now,
        provider_notes={"uw_backtest": uw_reason},
    )


def _in_blackout(events: list[dict], now: datetime, settings: Settings) -> bool:
    for evt in events:
        release = datetime.fromisoformat(str(evt["release_at"]).replace("Z", "+00:00"))
        if release.date() != now.date():
            continue
        before = timedelta(minutes=settings.macro_tier1_blackout_before_min)
        after = timedelta(minutes=settings.macro_tier1_blackout_after_min)
        if release - before <= now <= release + after:
            return True
    return False