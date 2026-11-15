from __future__ import annotations

from datetime import date

import pytest

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research import alphavantage_ingest


def test_ingest_requires_key():
    with pytest.raises(RuntimeError, match="ALPHAVANTAGE_API_KEY"):
        alphavantage_ingest.ingest_range(
            Settings(ALPHAVANTAGE_API_KEY=""),
            start=date(2026, 7, 2),
            end=date(2026, 7, 2),
        )


def test_ingest_writes_minute_from_intraday(tmp_path, monkeypatch):
    settings = Settings(ALPHAVANTAGE_API_KEY="k", UNDERLYING="SPY")
    root = tmp_path / "alphavantage"
    monkeypatch.setattr(alphavantage_ingest, "ALPHAVANTAGE_ROOT", root)
    monkeypatch.setattr(alphavantage_ingest, "throttle_between_calls", lambda _s: None)
    monkeypatch.setattr(
        alphavantage_ingest,
        "fetch_time_series_daily",
        lambda **kwargs: {"Time Series (Daily)": {}},
    )
    monkeypatch.setattr(
        alphavantage_ingest,
        "fetch_time_series_intraday",
        lambda **kwargs: {
            "Time Series (1min)": {
                "2026-07-02 11:00:00": {
                    "1. open": "100",
                    "2. high": "101",
                    "3. low": "99",
                    "4. close": "100.5",
                    "5. volume": "1",
                }
            }
        },
    )
    day = date(2026, 7, 2)
    stats = alphavantage_ingest.ingest_range(settings, start=day, end=day)
    assert stats["days_minute_written"] == 1
    minute = root / "minute" / "2026-07-02.json"
    assert minute.is_file()