from __future__ import annotations

from datetime import date

import pytest

from odte_spy_ibkr.alphavantage.client import (
    AlphaVantageError,
    av_query,
    fetch_global_quote,
    normalize_daily_rows,
    normalize_intraday_rows,
    split_intraday_by_session_date,
)


def test_normalize_intraday_rows_maps_alpaca_shape():
    payload = {
        "Time Series (1min)": {
            "2026-07-02 11:00:00": {
                "1. open": "545.1",
                "2. high": "545.2",
                "3. low": "545.0",
                "4. close": "545.15",
                "5. volume": "1200",
            }
        }
    }
    rows = normalize_intraday_rows(payload)
    assert len(rows) == 1
    assert rows[0]["o"] == 545.1
    assert rows[0]["c"] == 545.15
    assert "t" in rows[0]


def test_split_intraday_by_session_date():
    bars = [{"t": "2026-07-02T11:00:00-05:00", "c": 1.0}, {"t": "2026-07-03T10:00:00-05:00", "c": 2.0}]
    by = split_intraday_by_session_date(bars)
    assert set(by.keys()) == {"2026-07-02", "2026-07-03"}


def test_fetch_global_quote_injected():
    def fake_get(_url: str, params: dict):
        assert params["function"] == "GLOBAL_QUOTE"
        return {"Global Quote": {"05. price": "500.5"}}

    price = fetch_global_quote(symbol="SPY", api_key="test-key", get_json=fake_get)
    assert price == 500.5


def test_av_query_raises_on_note():
    def fake_get(_url: str, _params: dict):
        return {"Note": "Thank you for using Alpha Vantage! Our standard API call frequency is 5 calls per minute."}

    with pytest.raises(AlphaVantageError, match="Thank you"):
        av_query(api_key="k", params={"function": "GLOBAL_QUOTE"}, get_json=fake_get)


def test_normalize_daily_rows_filters_dates():
    payload = {
        "Time Series (Daily)": {
            "2026-07-01": {
                "1. open": "1",
                "2. high": "2",
                "3. low": "0.5",
                "4. close": "1.5",
                "6. volume": "10",
            }
        }
    }
    rows = normalize_daily_rows(payload)
    assert rows[0]["c"] == 1.5
    assert date.fromisoformat(rows[0]["t"][:10]) == date(2026, 7, 1)