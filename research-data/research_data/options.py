from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import urllib.parse

from research_data.http import HttpCallable, HttpStatusError, LoaderError, send

ORATS_URL = "https://api.orats.io/datav2"
_MAX_TICKERS_PER_REQUEST = 10
_TIMEOUT_SECONDS = 120

# options_daily column -> field of ORATS's end-of-day core data.
_DECIMAL_FIELDS = {
    "iv_30d": "iv30d",
    "iv_percentile_1y": "ivPctile1y",
    "hv_20d": "orHv20d",
    "skew_slope": "slope",
    "contango": "contango",
}
_COUNT_FIELDS = {
    "call_volume": "cVolu",
    "put_volume": "pVolu",
    "call_open_interest": "cOi",
    "put_open_interest": "pOi",
}
_REQUESTED_FIELDS = ("ticker", "tradeDate", "updatedAt", *_DECIMAL_FIELDS.values(), *_COUNT_FIELDS.values())


@dataclass(frozen=True)
class OptionsDay:
    symbol: str
    trade_date: date
    # Keyed by options_daily column; None where ORATS has no value.
    metrics: dict[str, float | int | None]
    source_updated_at: datetime | None


def _decimal(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(value)
    return float(value)


def _count(value: object) -> int | None:
    number = _decimal(value)
    if number is None:
        return None
    if number != int(number):
        raise ValueError(value)
    return int(number)


def parse_cores(body: bytes) -> list[OptionsDay]:
    try:
        records = json.loads(body)["data"]
        if not isinstance(records, list):
            raise TypeError(records)
    except (ValueError, KeyError, TypeError):
        raise LoaderError("Unexpected ORATS response: no data list") from None
    days: list[OptionsDay] = []
    for record in records:
        try:
            symbol = record["ticker"]
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(symbol)
            trade_date = date.fromisoformat(record["tradeDate"])
            updated = record.get("updatedAt")
            updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None
            if updated_at is not None and updated_at.tzinfo is None:
                raise ValueError(updated)
            metrics: dict[str, float | int | None] = {
                **{column: _decimal(record.get(field)) for column, field in _DECIMAL_FIELDS.items()},
                **{column: _count(record.get(field)) for column, field in _COUNT_FIELDS.items()},
            }
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LoaderError(f"Unexpected ORATS core record: {json.dumps(record)[:200]}") from None
        days.append(OptionsDay(symbol, trade_date, metrics, updated_at))
    return days


def download_day(
    token: str,
    symbols: list[str],
    trade_date: date,
    http: HttpCallable = send,
) -> list[OptionsDay]:
    """Download ORATS end-of-day core metrics for `symbols` on one trade date.

    Asking for one date keeps every response to a row per symbol; without a date ORATS sends each
    ticker's whole history back to 2007.
    """
    days: list[OptionsDay] = []
    for offset in range(0, len(symbols), _MAX_TICKERS_PER_REQUEST):
        query = {
            "token": token,
            "ticker": ",".join(symbols[offset : offset + _MAX_TICKERS_PER_REQUEST]),
            "tradeDate": trade_date.isoformat(),
            "fields": ",".join(_REQUESTED_FIELDS),
        }
        try:
            response = http("GET", f"{ORATS_URL}/hist/cores?{urllib.parse.urlencode(query)}", {}, None, _TIMEOUT_SECONDS)
        except HttpStatusError as exc:
            if exc.status == 404:
                # Nothing for these tickers on this date: a holiday, or a day ORATS has not published yet.
                continue
            raise
        days.extend(parse_cores(response.body))
    return days
