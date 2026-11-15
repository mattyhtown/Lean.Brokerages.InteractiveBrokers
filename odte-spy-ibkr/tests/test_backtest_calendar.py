"""ET 0DTE session calendar: NYSE holidays, early closes, and which sessions have a same-day expiry."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from odte_spy_ibkr.backtest.calendar import (
    has_odte_expiry,
    is_early_close,
    is_trading_day,
    iter_odte_sessions,
    iter_trading_days,
    market_holidays,
    session_close,
    session_open,
)
from odte_spy_ibkr.backtest.simulator import session_limits
from odte_spy_ibkr.config import Settings

ET = ZoneInfo("America/New_York")

# Published NYSE holiday schedules.
NYSE_HOLIDAYS = {
    2024: ["01-01", "01-15", "02-19", "03-29", "05-27", "06-19", "07-04", "09-02", "11-28", "12-25"],
    2025: ["01-01", "01-09", "01-20", "02-17", "04-18", "05-26", "06-19", "07-04", "09-01", "11-27", "12-25"],
    2026: ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"],
}


@pytest.mark.parametrize("year", sorted(NYSE_HOLIDAYS))
def test_holidays_match_the_published_nyse_schedule(year):
    expected = {date.fromisoformat(f"{year}-{md}") for md in NYSE_HOLIDAYS[year]}
    assert market_holidays(year) == expected


def test_holidays_reported_as_traded_are_not_sessions():
    """2026-07-03 (Independence Day observed) and 2026-09-07 (Labor Day) were backtested as sessions."""
    sessions = iter_odte_sessions(date(2026, 7, 1), date(2026, 11, 5))
    assert date(2026, 7, 3) not in sessions
    assert date(2026, 9, 7) not in sessions
    assert not is_trading_day(date(2026, 7, 3)) and not is_trading_day(date(2026, 9, 7))
    # 92 weekdays in the range, less the two holidays.
    assert len(sessions) == 90
    assert sessions == iter_trading_days(date(2026, 7, 1), date(2026, 11, 5))


def test_saturday_new_year_is_not_observed_on_friday():
    assert is_trading_day(date(2021, 12, 31))
    assert date(2021, 12, 31) not in market_holidays(2021)


def test_weekends_are_not_sessions():
    assert iter_trading_days(date(2024, 6, 1), date(2024, 6, 2)) == []


@pytest.mark.parametrize(
    ("day", "early"),
    [
        (date(2026, 11, 27), True),  # day after Thanksgiving
        (date(2026, 12, 24), True),  # Christmas Eve
        (date(2025, 7, 3), True),  # eve of a weekday Independence Day
        (date(2024, 7, 3), True),
        (date(2026, 7, 2), False),  # July 4 observed on Friday the 3rd; the 2nd is a full day
        (date(2026, 7, 3), False),  # a holiday, not a short session
        (date(2027, 12, 24), False),  # Christmas observed
        (date(2026, 11, 25), False),
    ],
)
def test_early_closes(day, early):
    assert is_early_close(day) is early
    if is_trading_day(day):
        assert session_close(day) == datetime.combine(day, time(13, 0) if early else time(16, 0), tzinfo=ET)


def test_session_times_are_eastern_across_the_dst_change():
    winter, summer = date(2024, 1, 16), date(2024, 7, 16)
    assert session_open(winter).utcoffset().total_seconds() == -5 * 3600
    assert session_open(summer).utcoffset().total_seconds() == -4 * 3600
    assert session_open(summer).time() == time(9, 30)


def test_half_day_moves_entry_cutoff_and_flatten_ahead_of_the_close(tmp_path):
    settings = Settings(BACKTEST_CACHE_DIR=str(tmp_path))
    full = session_limits(settings, date(2026, 11, 25))
    half = session_limits(settings, date(2026, 11, 27))
    assert (full.entry_cutoff, full.flatten_time) == (settings.entry_cutoff, settings.flatten_time)
    assert half.flatten_time < time(13, 0) and half.entry_cutoff < half.flatten_time


def test_spy_same_day_expiries_before_daily_listings():
    # Before 2022-11-15 SPY listed Monday/Wednesday/Friday expiries only.
    week = iter_trading_days(date(2022, 10, 3), date(2022, 10, 7))
    assert [d.weekday() for d in week if has_odte_expiry(d, "SPY")] == [0, 2, 4]
    assert has_odte_expiry(date(2022, 11, 15), "SPY") and has_odte_expiry(date(2022, 11, 17), "SPY")
    assert not has_odte_expiry(date(2026, 9, 7), "SPY")  # holiday
