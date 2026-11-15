from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from odte_spy_ibkr.chain.skew import ChainSkewSnapshot
from odte_spy_ibkr.config import MacroBacktestMode, Settings, UWBacktestMode
from odte_spy_ibkr.providers.base import FlowBias, MarketContext, VolRegime
from odte_spy_ibkr.providers.macro_news import (
    event_release_time,
    load_headline_records,
    load_macro_calendar_for_year,
    score_visible_headlines,
    tier1_blackout_active,
    visible_macro_events,
)
from odte_spy_ibkr.providers.uw_backtest import UwBacktestReplay

ET = ZoneInfo("America/New_York")

ENTRY_BLOCK_NOTE = "backtest_entry_block"


class SessionReplay:
    """Point-in-time provider context for one session.

    Cached inputs are loaded once; `context(now, ...)` exposes only what was known at
    `now`: headlines published by then, UW snapshots collected by then, and the scheduled
    calendar with outcomes of unreleased events removed.
    """

    def __init__(self, settings: Settings, day: date) -> None:
        self._settings = settings
        self.day = day
        enabled = settings.providers_backtest_enabled_set
        self._uw = UwBacktestReplay(settings) if "unusual_whales" in enabled else None
        self._macro = settings.macro_enabled and "macro_news" in enabled
        self._events: list[dict] = []
        self._headlines = None
        if self._macro:
            self._events = [
                evt
                for evt in load_macro_calendar_for_year(settings, day.year)
                if (release := event_release_time(evt)) is not None and release.astimezone(ET).date() == day
            ]
            self._headlines = load_headline_records(settings, day)

    @property
    def skip_reason(self) -> str | None:
        """Why this session must be left out of the scored sample, if it must."""
        if (
            self._uw is not None
            and self._settings.uw_backtest_mode == UWBacktestMode.SKIP
            and self._uw.load_snapshots(self.day) is None
        ):
            return "uw_cache_missing_skip"
        if (
            self._macro
            and self._settings.macro_backtest_mode == MacroBacktestMode.STRICT
            and self._headlines is None
        ):
            return "macro_news_missing_strict"
        return None

    def context(
        self,
        now: datetime,
        *,
        spot: float | None,
        chain_skew: ChainSkewSnapshot | None = None,
    ) -> MarketContext:
        bias, strength, block, uw_reason = FlowBias.NEUTRAL, 0.0, False, "uw_disabled"
        if self._uw is not None:
            bias, strength, block, uw_reason = self._uw.flow_signal(self.day, now)

        headline_score = score_visible_headlines(
            self._headlines or [],
            now,
            lookback_min=self._settings.macro_headline_lookback_min,
        )
        notes = {"uw_backtest": uw_reason}
        if block:
            notes[ENTRY_BLOCK_NOTE] = uw_reason
        return MarketContext(
            underlying=self._settings.underlying,
            spot=spot,
            flow_bias=bias,
            flow_strength=strength,
            vol_regime=VolRegime.NORMAL,
            macro_risk_off=headline_score < -0.6,
            net_call_bias=chain_skew.net_call_bias if chain_skew else 0.0,
            chain_skew=chain_skew,
            macro_headline_score=headline_score,
            macro_event_blackout=tier1_blackout_active(self._events, now, self._settings),
            as_of=now,
            provider_notes=notes,
            raw_by_provider={"macro_news": {"events": visible_macro_events(self._events, now)}},
        )


def build_backtest_context(
    settings: Settings,
    *,
    day: date,
    spot: float,
    chain_skew: ChainSkewSnapshot | None = None,
    now: datetime | None = None,
) -> MarketContext | None:
    """One-off context at `now` (default 11:00 ET); None when the session must be skipped.

    With UW_BACKTEST_MODE=block and no UW cache the context carries
    `provider_notes["backtest_entry_block"]`; the simulator opens no position on it.
    """
    now = now or datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=11, minute=0)
    replay = SessionReplay(settings, day)
    if replay.skip_reason is not None:
        return None
    return replay.context(now, spot=spot, chain_skew=chain_skew)
