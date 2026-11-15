from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from odte_spy_ibkr.chain import build_chain_skew
from odte_spy_ibkr.chain.legs import OptionCandidate
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.macro_news import load_macro_calendar_for_year
from odte_spy_ibkr.research.macro_surprise import macro_tier1_surprise_before_signal

ET = ZoneInfo("America/New_York")
SIGNAL_TIME = time(11, 0)
RTH_CLOSE = time(16, 0)


@dataclass(frozen=True)
class FeatureRow:
    day: date
    net_call_bias: float | None
    overnight_gap: float | None
    opening_range_pct: float | None
    intraday_mom_to_signal: float | None
    realized_vol_opening_hour: float | None
    macro_minutes_to_tier1: float | None
    macro_tier1_surprise: float | None
    uw_flow_strength: float | None  # None = untested / no cache


def _bar_ts(bar: dict) -> datetime:
    raw = bar["t"]
    if raw.endswith("Z"):
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
    return datetime.fromisoformat(raw)


def _price_at(minute_bars: list[dict], target: time) -> float | None:
    for b in minute_bars:
        ts = _bar_ts(b)
        if ts.time() >= target:
            return float(b["o"])
    return None


def _close_price(minute_bars: list[dict]) -> float | None:
    if not minute_bars:
        return None
    return float(minute_bars[-1]["c"])


def macro_minutes_to_release(settings: Settings, day: date, *, signal_at: datetime) -> float | None:
    events = load_macro_calendar_for_year(settings, day.year)
    best: float | None = None
    for evt in events:
        release = datetime.fromisoformat(str(evt["release_at"]).replace("Z", "+00:00")).astimezone(ET)
        if release.date() != day:
            continue
        delta = (release - signal_at).total_seconds() / 60.0
        if best is None or abs(delta) < abs(best):
            best = delta
    return best


def compute_features_for_day(
    settings: Settings,
    *,
    day: date,
    minute_bars: list[dict],
    prev_close: float | None,
    chain: list[OptionCandidate] | None,
) -> FeatureRow:
    open_px = _price_at(minute_bars, time(9, 30)) or (float(minute_bars[0]["o"]) if minute_bars else None)
    signal_px = _price_at(minute_bars, SIGNAL_TIME)
    high_or = None
    low_or = None
    vols: list[float] = []
    for b in minute_bars:
        ts = _bar_ts(b)
        if time(9, 30) <= ts.time() <= time(10, 30):
            high_or = b["h"] if high_or is None else max(high_or, b["h"])
            low_or = b["l"] if low_or is None else min(low_or, b["l"])
            if b.get("c"):
                vols.append(math.log(float(b["c"]) / float(b["o"])) if float(b["o"]) > 0 else 0.0)
    overnight_gap = None
    if open_px and prev_close and prev_close > 0:
        overnight_gap = (open_px - prev_close) / prev_close
    opening_range_pct = None
    if open_px and high_or is not None and low_or is not None and open_px > 0:
        opening_range_pct = (high_or - low_or) / open_px
    intraday_mom = None
    if open_px and signal_px and open_px > 0:
        intraday_mom = (signal_px - open_px) / open_px
    rv = None
    if len(vols) > 2:
        m = sum(vols) / len(vols)
        var = sum((x - m) ** 2 for x in vols) / len(vols)
        rv = math.sqrt(var) * math.sqrt(252 * 6.5 * 60)
    net_bias = None
    if chain and signal_px:
        skew = build_chain_skew(
            chain,
            expiry=day,
            spot=signal_px,
            as_of=datetime.combine(day, SIGNAL_TIME, tzinfo=ET),
        )
        net_bias = skew.net_call_bias
    signal_at = datetime.combine(day, SIGNAL_TIME, tzinfo=ET)
    macro_mins = macro_minutes_to_release(settings, day, signal_at=signal_at)
    macro_surprise = macro_tier1_surprise_before_signal(settings, day, signal_at=signal_at)
    return FeatureRow(
        day=day,
        net_call_bias=net_bias,
        overnight_gap=overnight_gap,
        opening_range_pct=opening_range_pct,
        intraday_mom_to_signal=intraday_mom,
        realized_vol_opening_hour=rv,
        macro_minutes_to_tier1=macro_mins,
        macro_tier1_surprise=macro_surprise,
        uw_flow_strength=None,
    )