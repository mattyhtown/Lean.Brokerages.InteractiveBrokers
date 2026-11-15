from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re
import urllib.parse

from research_data.http import HttpCallable, LoaderError, send

DATABENTO_URL = "https://hist.databento.com/v0"
# Consolidated end-of-day prices across all US exchanges.
DATASET = "EQUS.SUMMARY"
SCHEMA = "ohlcv-1d"

_PRICE_SCALE = Decimal(10**9)
_UNDEFINED_PRICE = 2**63 - 1
_MAX_SYMBOLS_PER_REQUEST = 2000
_TIMEOUT_SECONDS = 120
_UTC_TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?Z")


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class PriceDownload:
    bars: list[DailyBar]
    warnings: list[str]


def _auth_headers(api_key: str) -> dict[str, str]:
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _parse_utc(value: str) -> datetime:
    match = _UTC_TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(value)
    return datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def available_end(api_key: str, http: HttpCallable = send) -> str:
    """The exclusive end of the daily bars Databento has published so far, as Databento prints it."""
    url = f"{DATABENTO_URL}/metadata.get_dataset_range?{urllib.parse.urlencode({'dataset': DATASET})}"
    response = http("GET", url, _auth_headers(api_key), None, _TIMEOUT_SECONDS)
    try:
        payload = json.loads(response.body)
        end = payload.get("schema", {}).get(SCHEMA, {}).get("end") or payload["end"]
        _parse_utc(end)
    except (ValueError, KeyError, AttributeError, TypeError):
        raise LoaderError("Unexpected Databento dataset range response") from None
    return end


def parse_bars(body: bytes) -> tuple[list[DailyBar], int]:
    """Parse JSON-lines daily bars; also returns how many bars were dropped for missing prices."""
    bars: list[DailyBar] = []
    dropped = 0
    for line in body.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            raw_prices = [int(record[name]) for name in ("open", "high", "low", "close")]
            trade_date = datetime.fromtimestamp(int(record["hd"]["ts_event"]) // 10**9, tz=timezone.utc).date()
            symbol = record["symbol"]
            volume = int(record["volume"])
        except (ValueError, KeyError, TypeError):
            raise LoaderError(f"Unexpected Databento {SCHEMA} record: {line[:200]}") from None
        if not isinstance(symbol, str) or not symbol:
            raise LoaderError(f"Databento {SCHEMA} record has no symbol: {line[:200]}")
        if _UNDEFINED_PRICE in raw_prices or raw_prices[3] <= 0:
            dropped += 1
            continue
        prices = [Decimal(value) / _PRICE_SCALE for value in raw_prices]
        bars.append(DailyBar(symbol, trade_date, prices[0], prices[1], prices[2], prices[3], volume))
    return bars, dropped


def download_daily_bars(
    api_key: str,
    symbols: list[str],
    start: date,
    http: HttpCallable = send,
) -> PriceDownload:
    """Download daily bars for `symbols` from `start` through the latest published day."""
    end = available_end(api_key, http)
    if not symbols or datetime(start.year, start.month, start.day, tzinfo=timezone.utc) >= _parse_utc(end):
        return PriceDownload([], [])
    bars: list[DailyBar] = []
    warnings: list[str] = []
    for offset in range(0, len(symbols), _MAX_SYMBOLS_PER_REQUEST):
        form = {
            "dataset": DATASET,
            "schema": SCHEMA,
            "symbols": ",".join(symbols[offset : offset + _MAX_SYMBOLS_PER_REQUEST]),
            "start": start.isoformat(),
            "end": end,
            "encoding": "json",
            "map_symbols": "true",
        }
        headers = {**_auth_headers(api_key), "Content-Type": "application/x-www-form-urlencoded"}
        body = urllib.parse.urlencode(form).encode("utf-8")
        response = http("POST", f"{DATABENTO_URL}/timeseries.get_range", headers, body, _TIMEOUT_SECONDS)
        parsed, dropped = parse_bars(response.body)
        bars.extend(parsed)
        if dropped:
            warnings.append(f"Dropped {dropped} bars with a missing price")
        header = response.headers.get("x-warning")
        if header:
            try:
                warnings.extend(str(item) for item in json.loads(header))
            except ValueError:
                warnings.append(header)
    return PriceDownload(bars, warnings)
