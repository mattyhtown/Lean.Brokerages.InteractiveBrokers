from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ib_insync import IB, Stock, util

from odte_spy_ibkr.chain import OptionCandidate, filter_expiry, today_expiry
from odte_spy_ibkr.config import Settings

logger = logging.getLogger(__name__)


class IBSession:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.underlying = settings.underlying
        self._ib = IB()
        self._spot: float | None = None
        self._ticker_sub = None

    def is_connected(self) -> bool:
        return self._ib.isConnected()

    def connect(self) -> None:
        if self._ib.isConnected():
            return
        logger.info(
            "Connecting IB %s:%s clientId=%s paper=%s",
            self.settings.ib_host,
            self.settings.ib_port,
            self.settings.ib_client_id,
            self.settings.paper,
        )
        self._ib.connect(
            self.settings.ib_host,
            self.settings.ib_port,
            clientId=self.settings.ib_client_id,
            readonly=self.settings.read_only,
            account=self.settings.ib_account or "",
        )

    def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()

    def qualify_underlying(self) -> Stock:
        stock = Stock(self.underlying, "SMART", "USD")
        qualified = self._ib.qualifyContracts(stock)
        if not qualified:
            raise RuntimeError(f"Could not qualify {self.underlying}")
        return qualified[0]

    def stream_spot(self) -> None:
        stock = self.qualify_underlying()
        self._ticker_sub = self._ib.reqMktData(stock, "", False, False)
        self._ib.sleep(1)

    def get_spot_price(self, symbol: str | None = None) -> float | None:
        symbol = symbol or self.underlying
        if symbol != self.underlying:
            stock = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(stock)
            ticker = self._ib.reqMktData(stock, "", False, False)
            self._ib.sleep(0.5)
        else:
            if self._ticker_sub is None:
                self.stream_spot()
            ticker = self._ticker_sub

        for val in (ticker.marketPrice(), ticker.last, ticker.close):
            if val and val > 0:
                self._spot = float(val)
                return self._spot
        return self._spot

    def fetch_chain_candidates(self, spot: float | None = None) -> list[OptionCandidate]:
        stock = self.qualify_underlying()
        spot = spot or self.get_spot_price() or 0.0
        chains = self._ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
        if not chains:
            return []

        expiry = today_expiry()
        expiry_str = expiry.strftime("%Y%m%d")
        chain = next((c for c in chains if c.exchange in ("SMART", "ARCA", "NASDAQ")), chains[0])
        strikes = sorted(chain.strikes)
        if spot:
            strikes = [s for s in strikes if abs(s - spot) <= 25]

        from ib_insync import Option

        contracts = [
            Option(stock.symbol, expiry_str, strike, right, chain.exchange)
            for strike in strikes[:40]
            for right in ("P", "C")
        ]
        qualified = self._ib.qualifyContracts(*contracts)
        if not qualified:
            return []

        tickers = self._ib.reqTickers(*qualified)
        self._ib.sleep(1)

        out: list[OptionCandidate] = []
        for contract, ticker in zip(qualified, tickers, strict=False):
            greeks = ticker.modelGreeks
            delta = float(greeks.delta) if greeks and greeks.delta is not None else None
            out.append(
                OptionCandidate(
                    symbol=contract.localSymbol or contract.symbol,
                    strike=float(contract.strike),
                    right=contract.right,
                    expiry=expiry,
                    delta=delta,
                    bid=float(ticker.bid) if ticker.bid and ticker.bid > 0 else None,
                    ask=float(ticker.ask) if ticker.ask and ticker.ask > 0 else None,
                    volume=float(ticker.volume) if getattr(ticker, "volume", None) else None,
                    open_interest=float(ticker.openInterest)
                    if getattr(ticker, "openInterest", None)
                    else None,
                )
            )
        return filter_expiry(out, expiry)

    def place_combo_order(self, payload: dict[str, Any]) -> str | None:
        if not self.settings.orders_allowed:
            logger.warning("Orders disabled (ENABLE_ORDERS / safety flags)")
            return None
        logger.info("Would place combo order: %s", payload)
        return "dry-run"