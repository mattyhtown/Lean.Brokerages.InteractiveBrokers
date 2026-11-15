from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Public docs: https://orats.com/docs/historical-intraday-api
# Historical one-minute strikes chain (CSV). Requires subscription including Historical / Intraday Data API.
HIST_CHAIN_URL = "https://api.orats.io/datav2/historical/one-minute/strikes/chain"
DELAYED_CHAIN_URL = "https://api.orats.io/datav2/hist/one-minute/strikes/chain"


@dataclass(frozen=True)
class ORATSIngestNote:
    """Documented limits — do not assume plan depth beyond what the API returns."""

    historical_chain_path: str
    delayed_chain_path: str
    trade_date_format: str = "YYYYMMDDHHMM (America/New_York)"
    history_from: str = "August 2020 (per ORATS docs); Strikes-by-OPRA from Jan 2022"
    stock_prices: str = "ORATS does not provide equity OHLC — use Alpaca/IBKR for SPY minutes"
    plan_note: str = "Intraday chain history requires ORATS Intraday/Historical Data API subscription (see orats.com/data-api)"


ORATS_NOTES = ORATSIngestNote(
    historical_chain_path=HIST_CHAIN_URL,
    delayed_chain_path=DELAYED_CHAIN_URL,
)


class ORATSClient:
    def __init__(self, token: str, *, timeout: float = 120.0, use_delayed: bool = False) -> None:
        self._token = token.strip()
        self._timeout = timeout
        self._chain_url = DELAYED_CHAIN_URL if use_delayed else HIST_CHAIN_URL

    def is_configured(self) -> bool:
        return bool(self._token)

    def _get_csv(self, params: dict[str, str]) -> list[dict[str, str]]:
        params = {**params, "token": self._token}
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            resp = client.get(self._chain_url, params=params)
            resp.raise_for_status()
            text = resp.text.strip()
        if not text:
            return []
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)

    @staticmethod
    def trade_date_et(day: date, at: time) -> str:
        return datetime.combine(day, at, tzinfo=ET).strftime("%Y%m%d%H%M")

    def strikes_chain_at(
        self,
        ticker: str,
        day: date,
        *,
        at: time = time(11, 0),
    ) -> list[dict[str, str]]:
        return self._get_csv({"ticker": ticker, "tradeDate": self.trade_date_et(day, at)})