"""Point-in-time market data for one 0DTE session.

Every accessor takes the decision time `t` and only uses observations that were complete
at or before `t` (a 1-minute bar starting 10:59 is visible from 11:00).
"""

from __future__ import annotations

import logging
import random
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from odte_spy_ibkr.backtest.alpaca_history import (
    fetch_option_minute_bars,
    fetch_stock_minute_bars,
    occ_symbol,
    odte_symbols,
)
from odte_spy_ibkr.backtest.cache_audit import session_bar_rows
from odte_spy_ibkr.backtest.calendar import ET, session_close, session_open
from odte_spy_ibkr.backtest.data_loader import _cache_path, _rows_to_candidates, load_chain_for_day
from odte_spy_ibkr.backtest.store import read_rows, write_rows
from odte_spy_ibkr.chain import OptionCandidate, VerticalSpreadSelection, spread_intrinsic, today_expiry
from odte_spy_ibkr.chain.greeks import MINUTES_PER_YEAR, bs_delta, bs_price, implied_vol
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.alpaca import AlpacaDataError, alpaca_configured

logger = logging.getLogger(__name__)

SOURCE_OPTION_BARS = "option_bars"
SOURCE_CHAIN_SNAPSHOT = "chain_snapshot"
SOURCE_SYNTHETIC = "synthetic"

NO_CHAIN = "no_chain"
NO_SPOT = "no_spot_bars"
NOT_COMPLETED = "session_not_completed"

# Alpaca serves the most recent 15 minutes only to paid feeds; wait it out before fetching.
FETCH_DELAY_AFTER_CLOSE = timedelta(minutes=20)

LegKey = tuple[str, float]  # (right, strike)


@dataclass(frozen=True)
class Observation:
    """A price that became known at `at` (bar close time or snapshot time)."""

    at: datetime
    price: float
    volume: float = 0.0


@dataclass
class Series:
    obs: list[Observation]
    _times: list[datetime] = field(init=False, repr=False)
    _cum_volume: list[float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.obs = sorted(self.obs, key=lambda o: o.at)
        self._times = [o.at for o in self.obs]
        total = 0.0
        self._cum_volume = []
        for o in self.obs:
            total += o.volume
            self._cum_volume.append(total)

    def last_at(self, t: datetime) -> Observation | None:
        i = bisect_right(self._times, t)
        return self.obs[i - 1] if i else None

    def volume_until(self, t: datetime) -> float:
        i = bisect_right(self._times, t)
        return self._cum_volume[i - 1] if i else 0.0


def _parse_ts(raw: Any) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _bar_series(rows: list[dict[str, Any]]) -> Series:
    return Series(
        [
            Observation(_parse_ts(r["t"]) + timedelta(minutes=1), float(r["c"]), float(r.get("v") or 0))
            for r in rows
        ]
    )


@dataclass
class SessionData:
    day: date
    ticker: str
    source: str
    spot: Series
    legs: dict[LegKey, Series] = field(default_factory=dict)
    snapshot: list[OptionCandidate] = field(default_factory=list)
    snapshot_at: datetime | None = None
    mark_max_age: timedelta = timedelta(minutes=5)
    half_spread: float = 0.01
    _iv_cache: dict[tuple[LegKey, datetime], float | None] = field(default_factory=dict, repr=False)

    @property
    def open_at(self) -> datetime:
        return session_open(self.day)

    @property
    def close_at(self) -> datetime:
        return session_close(self.day)

    def spot_at(self, t: datetime) -> float | None:
        o = self.spot.last_at(t)
        return o.price if o else None

    def settlement_spot(self) -> float | None:
        return self.spot_at(self.close_at)

    def _years_to_close(self, t: datetime) -> float:
        return max(0.0, (self.close_at - t).total_seconds() / 60.0) / MINUTES_PER_YEAR

    def _fresh(self, o: Observation | None, t: datetime) -> bool:
        return o is not None and t - o.at <= self.mark_max_age

    def _implied_vol(self, key: LegKey, o: Observation) -> float | None:
        cache_key = (key, o.at)
        if cache_key not in self._iv_cache:
            spot = self.spot_at(o.at)
            self._iv_cache[cache_key] = (
                implied_vol(o.price, spot, key[1], self._years_to_close(o.at), key[0]) if spot else None
            )
        return self._iv_cache[cache_key]

    def chain_at(self, t: datetime, *, greeks: bool = True) -> list[OptionCandidate]:
        """Tradable chain at `t`: only legs with an observation no older than `mark_max_age`."""
        if self.snapshot_at is not None:
            if not (self.snapshot_at <= t <= self.snapshot_at + self.mark_max_age):
                return []
            if not greeks:
                return list(self.snapshot)
            return [self._with_delta(c, t) for c in self.snapshot]

        spot = self.spot_at(t)
        expiry = today_expiry(self.day)
        out: list[OptionCandidate] = []
        for key, series in self.legs.items():
            o = series.last_at(t)
            if not self._fresh(o, t):
                continue
            right, strike = key
            bid = max(0.0, o.price - self.half_spread)
            delta = None
            if greeks and spot:
                vol = self._implied_vol(key, o)
                if vol is not None:
                    delta = bs_delta(spot, strike, self._years_to_close(t), vol, right)
            out.append(
                OptionCandidate(
                    symbol=occ_symbol(self.ticker, self.day, right, strike),
                    strike=strike,
                    right=right,  # type: ignore[arg-type]
                    expiry=expiry,
                    delta=delta,
                    bid=bid,
                    ask=2.0 * o.price - bid,
                    volume=series.volume_until(t),
                    open_interest=0.0,
                )
            )
        return out

    def _with_delta(self, c: OptionCandidate, t: datetime) -> OptionCandidate:
        spot = self.spot_at(t)
        if c.delta is not None or c.mid is None or not spot:
            return c
        vol = implied_vol(c.mid, spot, c.strike, self._years_to_close(t), c.right)
        if vol is None:
            return c
        delta = bs_delta(spot, c.strike, self._years_to_close(t), vol, c.right)
        return replace(c, delta=delta)

    def leg_mark(self, right: str, strike: float, t: datetime) -> float | None:
        """Last observed price if fresh, otherwise a model reprice from its implied vol."""
        key = (right, strike)
        series = self.legs.get(key)
        o = series.last_at(t) if series else None
        if o is None:
            return None
        if self._fresh(o, t):
            return o.price
        spot = self.spot_at(t)
        vol = self._implied_vol(key, o)
        if spot is None or vol is None:
            return None
        return bs_price(spot, strike, self._years_to_close(t), vol, right)

    def spread_mark(self, spread: VerticalSpreadSelection, t: datetime) -> float | None:
        """Mid value of the vertical at `t`, clamped to [0, width]."""
        short = self.leg_mark(spread.short_leg.right, spread.short_leg.strike, t)
        long = self.leg_mark(spread.long_leg.right, spread.long_leg.strike, t)
        if short is None or long is None:
            return None
        return min(spread.width, max(0.0, short - long))

    def settlement_value(self, spread: VerticalSpreadSelection) -> float | None:
        spot = self.settlement_spot()
        return spread_intrinsic(spread, spot) if spot is not None else None


def spot_cache_path(settings: Settings, ticker: str, day: date) -> Path:
    return Path(settings.backtest_cache_dir) / "bars" / ticker / f"{day.isoformat()}.parquet"


def _session_from_bars(
    settings: Settings,
    day: date,
    spot_rows: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
    source: str,
) -> SessionData:
    by_leg: dict[LegKey, list[dict[str, Any]]] = {}
    for row in option_rows:
        by_leg.setdefault((str(row["right"]), float(row["strike"])), []).append(row)
    return SessionData(
        day=day,
        ticker=settings.underlying,
        source=source,
        spot=_bar_series(spot_rows),
        legs={key: _bar_series(rows) for key, rows in by_leg.items()},
        mark_max_age=timedelta(minutes=settings.backtest_mark_max_age_min),
        half_spread=settings.backtest_quote_half_spread,
    )


def _session_from_snapshot(
    settings: Settings,
    day: date,
    spot_rows: list[dict[str, Any]],
    snapshot: list[OptionCandidate],
    snapshot_at: datetime,
) -> SessionData:
    legs = {
        (c.right, c.strike): Series([Observation(snapshot_at, c.mid)]) for c in snapshot if c.mid is not None
    }
    return SessionData(
        day=day,
        ticker=settings.underlying,
        source=SOURCE_CHAIN_SNAPSHOT,
        spot=_bar_series(spot_rows),
        legs=legs,
        snapshot=snapshot,
        snapshot_at=snapshot_at,
        mark_max_age=timedelta(minutes=settings.backtest_mark_max_age_min),
        half_spread=settings.backtest_quote_half_spread,
    )


def _snapshot_time(settings: Settings, day: date, snapshot: list[OptionCandidate]) -> datetime:
    """Recorded capture time when the cache file is the snapshot in use, else the configured time."""
    rows = read_rows(_cache_path(settings, settings.underlying, day)) or []
    if rows and rows[0].get("snapshot_at") and _rows_to_candidates(rows, day) == snapshot:
        return _parse_ts(rows[0]["snapshot_at"])
    return datetime.combine(day, settings.backtest_snapshot_time, tzinfo=ET)


def synthetic_session(settings: Settings, day: date, *, spot0: float = 450.0, vol: float = 0.15) -> SessionData:
    """Seeded random-walk session priced with Black-Scholes. For pipeline smoke runs only."""
    rng = random.Random(day.toordinal())
    step_sd = vol / (252.0 * 390.0) ** 0.5
    close = session_close(day)
    spot_rows: list[dict[str, Any]] = []
    option_rows: list[dict[str, Any]] = []
    spot = spot0
    t = session_open(day)
    strikes = range(int(spot0) - settings.backtest_strike_range, int(spot0) + settings.backtest_strike_range + 1)
    while t < close:
        spot *= 1.0 + rng.gauss(0.0, step_sd)
        stamp = t.astimezone(timezone.utc).isoformat()
        spot_rows.append({"t": stamp, "o": spot, "h": spot, "l": spot, "c": spot, "v": 1000.0})
        years = max(0.0, (close - t).total_seconds() / 60.0 - 1.0) / MINUTES_PER_YEAR
        for strike in strikes:
            for right in ("P", "C"):
                price = round(bs_price(spot, float(strike), years, vol, right), 2)
                if price >= 0.01:
                    option_rows.append(
                        {"strike": float(strike), "right": right, "t": stamp, "c": price, "v": 10.0}
                    )
        t += timedelta(minutes=1)
    return _session_from_bars(settings, day, spot_rows, option_rows, SOURCE_SYNTHETIC)


@dataclass(frozen=True)
class SessionLoad:
    """Outcome of loading one session.

    `missing` says what real data the day lacks (NO_CHAIN, NO_SPOT, NOT_COMPLETED) and is
    None when the session replays that day's own chain and spot. `session` is then real
    data; with `missing` set it is None, or a generated session when synthetic data was
    explicitly requested.
    """

    session: SessionData | None
    missing: str | None = None
    has_own_chain: bool = False


def load_session(
    settings: Settings,
    day: date,
    *,
    allow_synthetic: bool = False,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> SessionLoad:
    """Cache first, then Alpaca history requested for `day`'s own session window.

    Nothing stands in for missing history: no other day's data, no live snapshot. A failed
    or empty fetch caches nothing, so a later run retries. Synthetic data is used only with
    `allow_synthetic=True` and is labelled as such.
    """
    ticker = settings.underlying
    now = now or datetime.now(tz=ET)
    if now < session_close(day):
        return SessionLoad(None, NOT_COMPLETED)
    can_fetch = (
        alpaca_configured(settings)
        and "alpaca" in settings.providers_backtest_enabled_set
        and now >= session_close(day) + FETCH_DELAY_AFTER_CLOSE
    )
    spot_path = spot_cache_path(settings, ticker, day)
    chain_path = _cache_path(settings, ticker, day)

    spot_rows = session_bar_rows(read_rows(spot_path), day)
    if not spot_rows and can_fetch:
        try:
            spot_rows = session_bar_rows(fetch_stock_minute_bars(settings, ticker, day, client=client), day)
        except AlpacaDataError as exc:
            logger.warning("Alpaca stock bars unavailable for %s %s: %s", ticker, day, exc)
        if spot_rows:
            write_rows(spot_path, spot_rows)

    option_rows = session_bar_rows(read_rows(chain_path), day)
    snapshot = [] if option_rows else load_chain_for_day(settings, ticker=ticker, day=day)
    if spot_rows and not option_rows and not snapshot and can_fetch:
        symbols = odte_symbols(ticker, day, float(spot_rows[0]["o"]), settings.backtest_strike_range)
        try:
            option_rows = session_bar_rows(fetch_option_minute_bars(settings, symbols, day, client=client), day)
        except AlpacaDataError as exc:
            logger.warning("Alpaca option bars unavailable for %s %s: %s", ticker, day, exc)
        if option_rows:
            write_rows(chain_path, option_rows)

    has_chain = bool(option_rows or snapshot)
    if has_chain and spot_rows:
        if option_rows:
            session = _session_from_bars(settings, day, spot_rows, option_rows, SOURCE_OPTION_BARS)
        else:
            session = _session_from_snapshot(
                settings, day, spot_rows, snapshot, _snapshot_time(settings, day, snapshot)
            )
        return SessionLoad(session, None, has_own_chain=True)
    return SessionLoad(
        synthetic_session(settings, day) if allow_synthetic else None,
        NO_SPOT if has_chain else NO_CHAIN,
        has_own_chain=has_chain,
    )
