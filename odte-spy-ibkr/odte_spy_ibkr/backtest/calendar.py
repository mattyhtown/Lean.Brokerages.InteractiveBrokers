from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def iter_trading_days(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def session_open(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)


def session_close(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)