#!/usr/bin/env python3
"""Minimal IB Gateway MCP bridge (read-first). Requires TWS or IB Gateway with API enabled."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ib-lean-bridge")

IB_HOST = os.environ.get("IB_HOST", "127.0.0.1")
IB_PORT = int(os.environ.get("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", "77"))


def _connect_params() -> dict[str, Any]:
    return {
        "host": IB_HOST,
        "port": IB_PORT,
        "clientId": IB_CLIENT_ID,
    }


@mcp.tool()
def ping() -> str:
    """Return configured IB connection parameters (no network call)."""
    return json.dumps(
        {
            "host": IB_HOST,
            "port": IB_PORT,
            "clientId": IB_CLIENT_ID,
            "hint": "Start IB Gateway/TWS, then call get_historical_bars.",
        },
        indent=2,
    )


@mcp.tool()
async def get_historical_bars(
    symbol: str,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    duration: str = "1 D",
    bar_size: str = "1 hour",
    what_to_show: str = "TRADES",
) -> str:
    """
    Fetch historical bars from Interactive Brokers.

    sec_type: STK (equity), CASH (forex), FUT (futures), BOND.
    Forex: symbol EURUSD as six letters, or symbol EUR with sec_type CASH, exchange IDEALPRO, currency USD.
    """
    try:
        from ib_insync import IB, Contract, util
    except ImportError as e:
        raise RuntimeError("Install dependencies: pip install -r requirements.txt") from e

    ib = IB()
    try:
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
        contract = Contract()
        contract.symbol = symbol
        contract.secType = sec_type
        contract.exchange = exchange
        contract.currency = currency
        if sec_type == "CASH" and len(symbol) == 6:
            contract.symbol = symbol[:3]
            contract.currency = symbol[3:]

        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract: {symbol} {sec_type}")

        bars = await ib.reqHistoricalDataAsync(
            qualified[0],
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=True,
            formatDate=1,
        )
        rows = [
            {
                "date": str(b.date),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
        return json.dumps({"symbol": symbol, "sec_type": sec_type, "bars": rows}, indent=2)
    finally:
        if ib.isConnected():
            ib.disconnect()


@mcp.tool()
async def get_contract_details(
    symbol: str,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
) -> str:
    """Resolve and return IB contract details (chain seed for options/futures/bonds)."""
    try:
        from ib_insync import IB, Contract
    except ImportError as e:
        raise RuntimeError("Install dependencies: pip install -r requirements.txt") from e

    ib = IB()
    try:
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
        contract = Contract()
        contract.symbol = symbol
        contract.secType = sec_type
        contract.exchange = exchange
        contract.currency = currency
        if sec_type == "CASH" and len(symbol) == 6:
            contract.symbol = symbol[:3]
            contract.currency = symbol[3:]

        details = await ib.reqContractDetailsAsync(contract)
        payload = []
        for d in details[:20]:
            c = d.contract
            payload.append(
                {
                    "conId": c.conId,
                    "symbol": c.symbol,
                    "localSymbol": c.localSymbol,
                    "secType": c.secType,
                    "exchange": c.exchange,
                    "currency": c.currency,
                    "lastTradeDateOrContractMonth": getattr(
                        c, "lastTradeDateOrContractMonth", None
                    ),
                }
            )
        return json.dumps({"count": len(details), "contracts": payload}, indent=2)
    finally:
        if ib.isConnected():
            ib.disconnect()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()