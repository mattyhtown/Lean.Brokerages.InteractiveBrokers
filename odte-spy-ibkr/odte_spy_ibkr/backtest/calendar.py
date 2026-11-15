from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Unscheduled full-day NYSE closures (national days of mourning, Sandy).
SPECIAL_CLOSURES = frozenset(
    {date(2012, 10, 29), date(2012, 10, 30), date(2018, 12, 5), date(2025, 1, 9)}
)

# SPY listed Tuesday expirations from 2022-11-15 and Thursday from 2022-11-17; before that
# only Monday/Wednesday/Friday sessions had a same-day expiry.
DAILY_EXPIRY_START = {"SPY": date(2022, 11, 15), "QQQ": date(2022, 11, 15)}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * i - h - k + m + 32) % 7
    month = (h - m + r + 90) // 25
    day = (h - m + r + month + 19) % 32
    return date(year, month, day)


def _observed(d: date) -> date | None:
    """NYSE rule: Sunday holidays move to Monday, Saturday holidays to Friday."""
    if d.weekday() == 6:
        return d + timedelta(days=1)
    if d.weekday() == 5:
        return d - timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def market_holidays(year: int) -> frozenset[date]:
    """Full-day NYSE closures for `year` (rule-based, plus known special closures)."""
    days: set[date] = set()
    new_year = date(year, 1, 1)
    # A Saturday New Year's Day is not observed on the prior Friday (year-end accounting).
    if new_year.weekday() != 5:
        days.add(_observed(new_year))
    days.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day
    days.add(_nth_weekday(year, 2, 0, 3))  # Presidents' Day
    days.add(_easter(year) - timedelta(days=2))  # Good Friday
    days.add(_last_weekday(year, 5, 0))  # Memorial Day
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days.add(_observed(date(year, 7, 4)))
    days.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    days.add(_observed(date(year, 12, 25)))
    days.update(d for d in SPECIAL_CLOSURES if d.year == year)
    return frozenset(d for d in days if d is not None and d.year == year)


def is_early_close(d: date) -> bool:
    """13:00 ET closes: day after Thanksgiving, Christmas Eve, and July 3 when they are sessions."""
    if not is_trading_day(d):
        return False
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    return (d.month, d.day) in {(7, 3), (12, 24)}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in market_holidays(d.year)


def has_odte_expiry(d: date, ticker: str = "SPY") -> bool:
    """True when `ticker` lists an option expiring on session `d` (a tradable 0DTE)."""
    if not is_trading_day(d):
        return False
    daily_from = DAILY_EXPIRY_START.get(ticker.upper())
    if daily_from is not None and d >= daily_from:
        return True
    return d.weekday() in (0, 2, 4)


def iter_trading_days(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def iter_odte_sessions(start: date, end: date, ticker: str = "SPY") -> list[date]:
    return [d for d in iter_trading_days(start, end) if has_odte_expiry(d, ticker)]


def session_open(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)


def session_close(d: date) -> datetime:
    close = EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE
    return datetime.combine(d, close, tzinfo=ET)
