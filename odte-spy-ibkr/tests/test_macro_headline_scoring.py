import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from odte_spy_ibkr.backtest.replay import SessionReplay
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers import macro_news
from odte_spy_ibkr.providers.macro_news import (
    MacroHeadline,
    load_headline_records,
    score_headline,
    score_visible_headlines,
)

DAY = date(2024, 6, 3)
NOON = datetime(2024, 6, 3, 16, 0, tzinfo=timezone.utc)


def headline(minutes_before_noon: int, sentiment: float) -> MacroHeadline:
    return MacroHeadline(NOON - timedelta(minutes=minutes_before_noon), "h", ["SPY"], sentiment)


def test_bearish_cpi_headline():
    assert score_headline("CPI inflation hotter than expected") < 0


def test_bullish_cut_headline():
    assert score_headline("Fed signals rate cut amid rally") > 0


def test_headline_without_keywords_is_neutral():
    assert score_headline("SPY ETF sees average volume") == 0.0
    assert score_headline("") == 0.0


def test_score_is_case_insensitive_and_bounded():
    assert score_headline("TARIFF WAR CRISIS") == score_headline("tariff war crisis")
    extreme = score_headline("cpi inflation tariff war sanction crisis recession hawkish hot")
    assert -1.0 <= extreme < 0


def test_strongest_headline_in_the_window_wins():
    records = [headline(50, 0.2), headline(30, -0.8), headline(10, 0.4)]
    assert score_visible_headlines(records, NOON, lookback_min=60) == -0.8


def test_window_excludes_old_and_future_headlines():
    records = [headline(61, -0.9), headline(-1, -0.9), headline(60, 0.3)]
    assert score_visible_headlines(records, NOON, lookback_min=60) == 0.3
    assert score_visible_headlines([headline(61, -0.9)], NOON, lookback_min=60) == 0.0


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(BACKTEST_CACHE_DIR=str(tmp_path), **overrides)


def test_no_cache_and_no_keys_means_no_news_not_zero_news(tmp_path):
    assert load_headline_records(_settings(tmp_path), DAY) is None


def test_alpaca_news_is_paginated_scored_and_cached(tmp_path, monkeypatch):
    settings = _settings(tmp_path, ALPACA_API_KEY="key", ALPACA_API_SECRET="secret")
    pages = {
        None: {
            "news": [{"headline": "CPI inflation hot", "created_at": "2024-06-03T12:31:00Z", "symbols": ["SPY"]}],
            "next_page_token": "p2",
        },
        "p2": {
            "news": [
                {"headline": "Fed easing rally", "created_at": "2024-06-03T18:05:00Z", "symbols": ["SPY"]},
                {"headline": "", "created_at": "2024-06-03T18:06:00Z"},
            ],
            "next_page_token": None,
        },
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("page_token")])

    real_client = httpx.Client
    monkeypatch.setattr(
        "odte_spy_ibkr.providers.alpaca.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    after_close = datetime(2024, 6, 4, 12, 0, tzinfo=timezone.utc)
    records = load_headline_records(settings, DAY, now=after_close)

    assert [r.headline for r in records] == ["CPI inflation hot", "Fed easing rally"]
    assert records[0].sentiment < 0 < records[1].sentiment
    first = requests[0]
    assert first.url.path == "/v1beta1/news"
    assert first.url.params["symbols"] == "SPY"
    assert first.url.params["start"] == "2024-06-03T04:00:00Z"  # midnight ET
    assert first.url.params["end"] == "2024-06-04T04:00:00Z"
    assert first.headers["APCA-API-KEY-ID"] == "key"
    assert len(requests) == 2

    cached = json.loads((tmp_path / "macro_news" / "2024-06-03.json").read_text(encoding="utf-8"))
    assert len(cached["headlines"]) == 2
    # A cache written after the day ended is complete: no second fetch.
    assert len(load_headline_records(settings, DAY, now=after_close + timedelta(days=30))) == 2
    assert len(requests) == 2


def test_intraday_cache_is_refreshed_after_the_refresh_interval(tmp_path, monkeypatch):
    settings = _settings(tmp_path, ALPACA_API_KEY="key", ALPACA_API_SECRET="secret", MACRO_HEADLINE_REFRESH_SEC=120)
    calls = []

    def fake_fetch(_settings, day):
        calls.append(day)
        return [MacroHeadline(NOON, f"CPI inflation #{len(calls)}", ["SPY"], -0.5)]

    monkeypatch.setattr(macro_news, "_fetch_alpaca_news_records", fake_fetch)
    load_headline_records(settings, DAY, now=NOON)
    load_headline_records(settings, DAY, now=NOON + timedelta(seconds=60))
    assert len(calls) == 1
    latest = load_headline_records(settings, DAY, now=NOON + timedelta(seconds=181))
    assert len(calls) == 2
    assert latest[0].headline == "CPI inflation #2"


def test_failed_fetch_keeps_cached_headlines_and_caches_nothing_new(tmp_path, monkeypatch):
    settings = _settings(tmp_path, ALPACA_API_KEY="key", ALPACA_API_SECRET="secret")

    def boom(_settings, _day):
        raise macro_news.AlpacaDataError("HTTP 500")

    monkeypatch.setattr(macro_news, "_fetch_alpaca_news_records", boom)
    assert load_headline_records(settings, DAY, now=NOON) is None
    assert not (tmp_path / "macro_news").exists()


@pytest.mark.parametrize("mode,expected", [("permissive", None), ("strict", "macro_news_missing_strict")])
def test_missing_news_day_follows_macro_backtest_mode(tmp_path, mode, expected):
    settings = _settings(
        tmp_path,
        MACRO_BACKTEST_MODE=mode,
        UW_BACKTEST_CACHE_DIR=str(tmp_path / "uw"),
        UW_SESSION_JSONL_DIR=str(tmp_path / "uw" / "sessions"),
        MACRO_CALENDAR_PATH=str(tmp_path / "none.yaml"),
    )
    assert SessionReplay(settings, DAY).skip_reason == expected
