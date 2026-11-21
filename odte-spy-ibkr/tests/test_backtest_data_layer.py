"""Alpaca historical loaders (mocked HTTP), the row cache, and Black-Scholes marks."""

from __future__ import annotations

import httpx
import pytest

from odte_spy_ibkr.backtest.alpaca_history import (
    fetch_option_minute_bars,
    fetch_stock_minute_bars,
    occ_symbol,
    odte_symbols,
    parse_occ_symbol,
)
from odte_spy_ibkr.backtest.cache_audit import symbol_expiry
from odte_spy_ibkr.backtest.data_loader import _cache_path
from odte_spy_ibkr.backtest.session_data import spot_cache_path
from odte_spy_ibkr.backtest.store import read_rows, write_rows
from odte_spy_ibkr.chain.greeks import MINUTES_PER_YEAR, bs_delta, bs_price, implied_vol
from odte_spy_ibkr.providers.alpaca import AlpacaDataError, alpaca_paged_get

from conftest import FIXTURE_DAY

DAY = FIXTURE_DAY


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_occ_symbol_round_trip():
    symbol = occ_symbol("spy", DAY, "P", 445.0)
    assert symbol == "SPY240603P00445000"
    assert parse_occ_symbol(symbol) == ("P", 445.0)
    assert occ_symbol("SPY", DAY, "C", 512.5) == "SPY240603C00512500"
    assert symbol_expiry(symbol) == DAY
    assert symbol_expiry("SPY   240603P00445000") == DAY  # IB local-symbol padding
    assert symbol_expiry("SPY") is None and symbol_expiry(None) is None


def test_odte_symbols_cover_both_rights_around_the_open():
    symbols = odte_symbols("SPY", DAY, 450.4, 2)
    assert len(symbols) == 10
    assert {parse_occ_symbol(s)[1] for s in symbols} == {448.0, 449.0, 450.0, 451.0, 452.0}
    assert all(s.startswith("SPY240603") for s in symbols)


def test_cache_paths_follow_the_plan_layout(fixture_settings):
    root = fixture_settings.backtest_cache_dir
    assert str(_cache_path(fixture_settings, "SPY", DAY)) == f"{root}/SPY/2024-06-03.parquet"
    assert str(spot_cache_path(fixture_settings, "SPY", DAY)) == f"{root}/bars/SPY/2024-06-03.parquet"


def test_parquet_cache_round_trip(tmp_path):
    rows = [
        {"symbol": "SPY240603P00445000", "strike": 445.0, "right": "P", "t": "2024-06-03T13:30:00Z", "c": 0.4, "v": 10.0},
        {"symbol": "SPY240603P00444000", "strike": 444.0, "right": "P", "t": "2024-06-03T13:31:00Z", "c": 0.15, "v": 3.0},
    ]
    path = write_rows(tmp_path / "SPY" / "2024-06-03.parquet", rows)
    assert path.suffix == ".parquet"
    assert read_rows(path) == rows
    assert read_rows(tmp_path / "SPY" / "2024-06-04.parquet") is None


def test_stock_bars_request_one_session_and_follow_pagination(keyed_settings):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "page_token" not in request.url.params:
            return httpx.Response(200, json={"bars": [{"t": "2024-06-03T13:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 7}], "next_page_token": "p2"})
        return httpx.Response(200, json={"bars": [{"t": "2024-06-03T13:31:00Z", "o": 2, "h": 2, "l": 2, "c": 2}], "next_page_token": None})

    rows = fetch_stock_minute_bars(keyed_settings, "SPY", DAY, client=client_for(handler))
    assert [row["c"] for row in rows] == [1.5, 2.0]
    assert rows[1]["v"] == 0.0
    first, second = seen
    assert first.url.path == "/v2/stocks/SPY/bars"
    assert first.url.params["start"] == "2024-06-03T13:30:00Z" and first.url.params["end"] == "2024-06-03T20:00:00Z"
    assert first.url.params["timeframe"] == "1Min" and first.url.params["adjustment"] == "raw"
    assert second.url.params["page_token"] == "p2"
    assert first.headers["APCA-API-KEY-ID"] == "test-key" and first.headers["APCA-API-SECRET-KEY"] == "test-secret"


def test_option_bars_are_batched_and_keyed_by_contract(keyed_settings, monkeypatch):
    monkeypatch.setattr("odte_spy_ibkr.backtest.alpaca_history.MAX_SYMBOLS_PER_REQUEST", 2)
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        symbols = request.url.params["symbols"].split(",")
        batches.append(symbols)
        bars = {s: [{"t": "2024-06-03T14:00:00Z", "o": 0.4, "h": 0.4, "l": 0.4, "c": 0.4, "v": 5}] for s in symbols[:1]}
        return httpx.Response(200, json={"bars": bars, "next_page_token": None})

    symbols = [occ_symbol("SPY", DAY, "P", k) for k in (443.0, 444.0, 445.0)]
    rows = fetch_option_minute_bars(keyed_settings, symbols, DAY, client=client_for(handler))
    assert batches == [symbols[:2], symbols[2:]]
    assert [(r["symbol"], r["right"], r["strike"]) for r in rows] == [(symbols[0], "P", 443.0), (symbols[2], "P", 445.0)]


def test_rate_limit_is_retried_then_other_errors_raise(keyed_settings):
    statuses = iter([429, 429, 200])
    sleeps: list[float] = []
    client = client_for(lambda request: httpx.Response(next(statuses), json={"bars": []}))
    pages = list(alpaca_paged_get(keyed_settings, "/v2/stocks/SPY/bars", {}, client=client, sleep=sleeps.append))
    assert pages == [{"bars": []}] and sleeps == [1.0, 2.0]

    with pytest.raises(AlpacaDataError, match="HTTP 403"):
        list(alpaca_paged_get(keyed_settings, "/x", {}, client=client_for(lambda r: httpx.Response(403, text="no"))))

    def refuse(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(AlpacaDataError, match="failed"):
        list(alpaca_paged_get(keyed_settings, "/x", {}, client=client_for(refuse)))


def test_no_request_is_made_without_credentials(fixture_settings):
    calls: list[httpx.Request] = []
    client = client_for(lambda request: calls.append(request) or httpx.Response(200, json={}))
    with pytest.raises(AlpacaDataError, match="not set"):
        fetch_stock_minute_bars(fixture_settings, "SPY", DAY, client=client)
    assert calls == []


def test_black_scholes_parity_and_implied_vol_round_trip():
    spot, strike, vol, years = 450.0, 445.0, 0.18, 180.0 / MINUTES_PER_YEAR
    call, put = bs_price(spot, strike, years, vol, "C"), bs_price(spot, strike, years, vol, "P")
    assert call - put == pytest.approx(spot - strike)  # zero rate, no dividends
    assert implied_vol(put, spot, strike, years, "P") == pytest.approx(vol, rel=1e-6)
    assert bs_delta(spot, strike, years, vol, "C") - bs_delta(spot, strike, years, vol, "P") == pytest.approx(1.0)
    assert -0.5 < bs_delta(spot, strike, years, vol, "P") < 0.0


def test_black_scholes_at_expiry_and_degenerate_prices():
    assert bs_price(450.0, 445.0, 0.0, 0.2, "P") == 0.0
    assert bs_price(440.0, 445.0, 0.0, 0.2, "P") == 5.0
    assert bs_delta(440.0, 445.0, 0.0, 0.2, "P") == -1.0
    assert implied_vol(5.0, 440.0, 445.0, 1e-4, "P") is None  # no time value
    assert implied_vol(0.4, 450.0, 445.0, 0.0, "P") is None
