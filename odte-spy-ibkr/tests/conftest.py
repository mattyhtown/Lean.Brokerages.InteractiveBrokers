from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx
import pytest

from odte_spy_ibkr.backtest.alpaca_history import occ_symbol
from odte_spy_ibkr.backtest.calendar import session_close, session_open
from odte_spy_ibkr.backtest.session_data import SessionData, _session_from_bars
from odte_spy_ibkr.config import Settings

FIXTURE_DAY = date(2024, 6, 3)  # Monday, regular session
LATER_DAY = date(2024, 6, 5)  # stands in for "today" as seen from FIXTURE_DAY

PriceFn = Callable[[datetime], float | None]


@pytest.fixture(autouse=True)
def _no_external_credentials(monkeypatch):
    """Tests never reach a vendor API, whatever the developer's environment holds."""
    for name in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "UNUSUAL_WHALES_API_KEY", "CONFIG_YAML"):
        monkeypatch.setenv(name, "")


def constant(price: float) -> PriceFn:
    return lambda _at: price


def build_session(
    settings: Settings,
    *,
    day: date = FIXTURE_DAY,
    spot: PriceFn,
    legs: dict[tuple[str, float], PriceFn],
    source: str = "option_bars",
) -> SessionData:
    """Session of 1-minute bars. Each price function receives the bar's close time (the
    moment the bar becomes visible) and may return None for a minute with no trade."""
    spot_rows: list[dict] = []
    option_rows: list[dict] = []
    t = session_open(day)
    while t < session_close(day):
        visible_at = t + timedelta(minutes=1)
        stamp = t.astimezone(timezone.utc).isoformat()
        price = spot(visible_at)
        spot_rows.append({"t": stamp, "o": price, "h": price, "l": price, "c": price, "v": 1000.0})
        for (right, strike), fn in legs.items():
            leg_price = fn(visible_at)
            if leg_price is not None:
                option_rows.append({"strike": strike, "right": right, "t": stamp, "c": leg_price, "v": 10.0})
        t += timedelta(minutes=1)
    return _session_from_bars(settings, day, spot_rows, option_rows, source)


@pytest.fixture
def fixture_settings(tmp_path) -> Settings:
    """Isolated caches, short strike chosen $5 below spot, no macro calendar on the fixture day."""
    calendar = tmp_path / "macro_events.yaml"
    calendar.write_text("events: []\n", encoding="utf-8")
    return Settings(
        BACKTEST_CACHE_DIR=str(tmp_path / "cache"),
        UW_BACKTEST_CACHE_DIR=str(tmp_path / "uw"),
        UW_SESSION_JSONL_DIR=str(tmp_path / "uw" / "sessions"),
        MACRO_CALENDAR_PATH=str(calendar),
        JOURNAL_PATH=str(tmp_path / "journal" / "daily_pnl.jsonl"),
        OTM_DISTANCE=5.0,
        MIN_CREDIT=0.15,
        SPREAD_WIDTH=1.0,
        BACKTEST_SLIPPAGE_TICKS=1.0,
        BACKTEST_COMMISSION_PER_CONTRACT=0.65,
    )


class FakeAlpaca:
    """Alpaca Market Data over httpx.MockTransport. Serves 1-minute bars for whatever
    window is requested, so a response is always the data of the day that was asked for."""

    def __init__(self, *, stock_status=200, option_status=200, option_bars=True):
        self.requests: list[httpx.Request] = []
        self.stock_status = stock_status
        self.option_status = option_status
        self.option_bars = option_bars
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    @staticmethod
    def _minutes(request: httpx.Request) -> list[str]:
        start = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00"))
        count = int((end - start).total_seconds() // 60)
        return [(start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(count)]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/v2/stocks/"):
            if self.stock_status != 200:
                return httpx.Response(self.stock_status, text="upstream error")
            bars = [{"t": t, "o": 450.0, "h": 450.0, "l": 450.0, "c": 450.0, "v": 1000} for t in self._minutes(request)]
            return httpx.Response(200, json={"bars": bars, "next_page_token": None})
        if path == "/v1beta1/options/bars":
            if self.option_status != 200:
                return httpx.Response(self.option_status, text="upstream error")
            bars = {}
            if self.option_bars:
                for symbol in request.url.params["symbols"].split(","):
                    price = 0.40 if symbol.endswith("P00445000") else 0.15
                    bars[symbol] = [{"t": t, "o": price, "h": price, "l": price, "c": price, "v": 10} for t in self._minutes(request)]
            return httpx.Response(200, json={"bars": bars, "next_page_token": None})
        # Anything else (notably /v1beta1/options/snapshots, which takes no date) is a bug.
        return httpx.Response(404, text=f"unexpected request {path}")


@pytest.fixture
def keyed_settings(fixture_settings):
    fixture_settings.alpaca_api_key = "test-key"
    fixture_settings.alpaca_api_secret = "test-secret"
    return fixture_settings


def chain_path(settings, day=FIXTURE_DAY) -> Path:
    return Path(settings.backtest_cache_dir) / settings.underlying / f"{day.isoformat()}.parquet"


def cache_files(settings) -> list[Path]:
    root = Path(settings.backtest_cache_dir)
    return sorted(p for p in root.rglob("*") if p.is_file())


def after_close(day=FIXTURE_DAY) -> datetime:
    return session_close(day) + timedelta(hours=2)


def snapshot_rows(expiry: date, *, snapshot_at: datetime | None) -> list[dict]:
    rows = [
        {"symbol": occ_symbol("SPY", expiry, "P", 445.0), "strike": 445.0, "right": "P", "bid": 0.39, "ask": 0.41},
        {"symbol": occ_symbol("SPY", expiry, "P", 444.0), "strike": 444.0, "right": "P", "bid": 0.14, "ask": 0.16},
    ]
    if snapshot_at is not None:
        rows = [{**row, "snapshot_at": snapshot_at.isoformat()} for row in rows]
    return rows


def spot_rows(day=FIXTURE_DAY) -> list[dict]:
    rows, t = [], session_open(day)
    while t < session_close(day):
        rows.append({"t": t.astimezone(timezone.utc).isoformat(), "o": 450.0, "h": 450.0, "l": 450.0, "c": 450.0, "v": 1000.0})
        t += timedelta(minutes=1)
    return rows
