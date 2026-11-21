#!/usr/bin/env python3
"""Minimal IB Gateway MCP bridge. Requires TWS or IB Gateway with API enabled.

Reads (history, contract details, open orders, order preview) work as soon as
Gateway is reachable. Sending or cancelling an order additionally requires
IB_ALLOW_ORDERS=1 in the server environment and confirm=true on the call.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from weakref import WeakKeyDictionary

from mcp.server.fastmcp import FastMCP

try:
    from ib_insync import IB, Contract, LimitOrder, MarketOrder, Order, OrderState
except ImportError:  # ping still answers without the IB client library
    IB = None

mcp = FastMCP("ib-lean-bridge")

# An unset ${env:...} in mcp.json can arrive as an empty string, so fall back on empty too.
IB_HOST = os.environ.get("IB_HOST") or "127.0.0.1"
IB_PORT = int(os.environ.get("IB_PORT") or "7497")
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID") or "77")
IB_ACCOUNT = os.environ.get("IB_ACCOUNT") or ""
ORDERS_ENABLED = os.environ.get("IB_ALLOW_ORDERS") == "1"

ORDER_SETTLE_SECONDS = 5.0
OPEN_ORDERS_TIMEOUT_SECONDS = 10.0
PENDING_STATUSES = {"PendingSubmit", "ApiPending", "PendingCancel"}
REJECTED_STATUSES = {"Inactive", "Cancelled", "ApiCancelled"}
IB_UNSET_DOUBLE = 1.7976931348623157e308

# One IB_CLIENT_ID cannot hold two concurrent TWS/Gateway sessions; serialize per event loop.
_SESSION_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


def _get_session_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _SESSION_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[loop] = lock
    return lock


def _require_ib() -> None:
    if IB is None:
        raise RuntimeError("Install dependencies: pip install -r requirements.txt")


def _clean_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value == IB_UNSET_DOUBLE or not math.isfinite(value):
            return None
        return float(value)
    return None


def _build_contract(
    symbol: str,
    sec_type: str,
    exchange: str,
    currency: str,
    expiry: str = "",
    con_id: int = 0,
) -> "Contract":
    _require_ib()
    contract = Contract()
    contract.secType = sec_type
    contract.exchange = exchange
    if con_id:
        contract.conId = con_id
        return contract
    contract.symbol = symbol
    contract.currency = currency
    if sec_type == "CASH" and len(symbol) == 6:
        contract.symbol = symbol[:3]
        contract.currency = symbol[3:]
    if expiry:
        contract.lastTradeDateOrContractMonth = expiry
    return contract


def _build_order(
    action: str,
    quantity: float,
    order_type: str,
    limit_price: float | None,
    tif: str,
    account: str,
) -> "Order":
    _require_ib()
    side = action.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("action must be BUY or SELL")
    if isinstance(quantity, bool) or not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("quantity must be a finite number greater than zero")
    kind = order_type.upper()
    if kind == "LMT":
        if (
            limit_price is None
            or isinstance(limit_price, bool)
            or not math.isfinite(limit_price)
            or limit_price <= 0
        ):
            raise ValueError("limit_price must be a finite number greater than zero for a LMT order")
        order = LimitOrder(side, quantity, limit_price)
    elif kind == "MKT":
        if limit_price is not None:
            raise ValueError("limit_price must not be set for a MKT order")
        order = MarketOrder(side, quantity)
    else:
        raise ValueError("order_type must be LMT or MKT")
    order.tif = tif.upper()
    if account or IB_ACCOUNT:
        order.account = account or IB_ACCOUNT
    return order


@asynccontextmanager
async def _session(readonly: bool) -> AsyncIterator[tuple["IB", list[str]]]:
    """Connect to Gateway/TWS and collect the error messages IB sends meanwhile."""
    _require_ib()
    async with _get_session_lock():
        ib = IB()
        messages: list[str] = []
        ib.errorEvent += lambda req_id, code, text, contract: messages.append(f"{code}: {text}")
        try:
            try:
                await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, readonly=readonly)
            except (OSError, asyncio.TimeoutError) as e:
                raise RuntimeError(
                    f"Could not connect to IB at {IB_HOST}:{IB_PORT} ({e or type(e).__name__}). "
                    "Start IB Gateway or TWS with the API enabled."
                ) from e
            yield ib, messages
        finally:
            if ib.isConnected():
                ib.disconnect()


async def _qualify(ib: "IB", contract: "Contract") -> "Contract":
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        raise ValueError(
            f"Could not resolve a single contract for {contract.symbol or contract.conId} "
            f"{contract.secType}. Use get_contract_details to pick one, then pass con_id."
        )
    return qualified[0]


def _contract_summary(c: "Contract") -> dict[str, Any]:
    return {
        "conId": c.conId,
        "symbol": c.symbol,
        "localSymbol": c.localSymbol,
        "secType": c.secType,
        "exchange": c.exchange,
        "currency": c.currency,
        "lastTradeDateOrContractMonth": c.lastTradeDateOrContractMonth,
    }


def _trade_summary(trade: Any) -> dict[str, Any]:
    order, status = trade.order, trade.orderStatus
    return {
        "orderId": order.orderId,
        "permId": order.permId,
        "clientId": order.clientId,
        "account": order.account,
        "action": order.action,
        "quantity": order.totalQuantity,
        "orderType": order.orderType,
        "limitPrice": _clean_float(order.lmtPrice),
        "tif": order.tif,
        "status": status.status,
        "filled": status.filled,
        "remaining": status.remaining,
        "avgFillPrice": status.avgFillPrice,
        "contract": _contract_summary(trade.contract),
    }


async def _wait_while_pending(trade: Any) -> None:
    waited = 0.0
    while trade.orderStatus.status in PENDING_STATUSES and waited < ORDER_SETTLE_SECONDS:
        await asyncio.sleep(0.25)
        waited += 0.25


def _require_orders_enabled(confirm: bool) -> None:
    if not ORDERS_ENABLED:
        raise PermissionError(
            "Order sending is off. Set IB_ALLOW_ORDERS=1 in the ib-lean-bridge server "
            "environment to turn it on."
        )
    if not confirm:
        raise PermissionError(
            "Pass confirm=true only after the user has approved this exact order."
        )


@mcp.tool()
def ping() -> str:
    """Return configured IB connection parameters (no network call)."""
    return json.dumps(
        {
            "host": IB_HOST,
            "port": IB_PORT,
            "clientId": IB_CLIENT_ID,
            "account": IB_ACCOUNT or None,
            "ordersEnabled": ORDERS_ENABLED,
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
    expiry: str = "",
    con_id: int = 0,
) -> str:
    """
    Fetch historical bars from Interactive Brokers.

    sec_type: STK (equity), CASH (forex), FUT (futures), BOND.
    Forex: symbol EURUSD as six letters, or symbol EUR with sec_type CASH, exchange IDEALPRO,
    currency USD. IB has no TRADES bars for forex, so TRADES is replaced by MIDPOINT.
    Futures: pass expiry as YYYYMM or YYYYMMDD and the listing exchange (e.g. CME).
    Bonds: pass con_id from get_contract_details.
    """
    if sec_type == "CASH" and what_to_show == "TRADES":
        what_to_show = "MIDPOINT"
    async with _session(readonly=True) as (ib, messages):
        contract = await _qualify(
            ib, _build_contract(symbol, sec_type, exchange, currency, expiry, con_id)
        )
        bars = await ib.reqHistoricalDataAsync(
            contract,
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
        return json.dumps(
            {
                "symbol": symbol,
                "sec_type": sec_type,
                "what_to_show": what_to_show,
                "contract": _contract_summary(contract),
                "bars": rows,
                "messages": messages,
            },
            indent=2,
        )


@mcp.tool()
async def get_contract_details(
    symbol: str,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    expiry: str = "",
) -> str:
    """Resolve and return IB contract details (chain seed for options/futures/bonds)."""
    async with _session(readonly=True) as (ib, messages):
        contract = _build_contract(symbol, sec_type, exchange, currency, expiry)
        details = await ib.reqContractDetailsAsync(contract)
        payload = [_contract_summary(d.contract) for d in details[:20]]
        return json.dumps(
            {"count": len(details), "contracts": payload, "messages": messages}, indent=2
        )


@mcp.tool()
async def get_open_orders() -> str:
    """List open orders on the IB account, from every API client and TWS itself."""
    async with _session(readonly=True) as (ib, messages):
        trades = await ib.reqAllOpenOrdersAsync()
        return json.dumps(
            {"orders": [_trade_summary(t) for t in trades], "messages": messages}, indent=2
        )


@mcp.tool()
async def preview_order(
    symbol: str,
    action: str,
    quantity: float,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    order_type: str = "LMT",
    limit_price: float | None = None,
    tif: str = "DAY",
    expiry: str = "",
    con_id: int = 0,
    account: str = "",
) -> str:
    """
    Ask IB what an order would do, without sending it (IB "what-if" check).

    Returns the margin change and commission IB estimates. Show this to the user and get
    their approval before calling place_order with the same arguments.
    action: BUY or SELL. order_type: LMT (needs limit_price) or MKT. tif: DAY or GTC.
    """
    order = _build_order(action, quantity, order_type, limit_price, tif, account)
    async with _session(readonly=False) as (ib, messages):
        contract = await _qualify(
            ib, _build_contract(symbol, sec_type, exchange, currency, expiry, con_id)
        )
        state = await ib.whatIfOrderAsync(contract, order)
        if not isinstance(state, OrderState):
            raise RuntimeError(f"IB rejected the preview: {'; '.join(messages) or 'no reason given'}")
        return json.dumps(
            {
                "sent": False,
                "contract": _contract_summary(contract),
                "order": {
                    "action": order.action,
                    "quantity": order.totalQuantity,
                    "orderType": order.orderType,
                    "limitPrice": _clean_float(order.lmtPrice),
                    "tif": order.tif,
                    "account": order.account or None,
                },
                "initMarginChange": state.initMarginChange,
                "maintMarginChange": state.maintMarginChange,
                "equityWithLoanChange": state.equityWithLoanChange,
                "initMarginAfter": state.initMarginAfter,
                "maintMarginAfter": state.maintMarginAfter,
                "equityWithLoanAfter": state.equityWithLoanAfter,
                "commission": _clean_float(state.commission),
                "minCommission": _clean_float(state.minCommission),
                "maxCommission": _clean_float(state.maxCommission),
                "commissionCurrency": state.commissionCurrency,
                "warningText": state.warningText,
                "messages": messages,
            },
            indent=2,
        )


@mcp.tool()
async def place_order(
    symbol: str,
    action: str,
    quantity: float,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    order_type: str = "LMT",
    limit_price: float | None = None,
    tif: str = "DAY",
    expiry: str = "",
    con_id: int = 0,
    account: str = "",
    confirm: bool = False,
) -> str:
    """
    Send an order to Interactive Brokers. This moves real money on a live account.

    Refused unless the server runs with IB_ALLOW_ORDERS=1 and confirm is true. Call
    preview_order first, show the result to the user, and set confirm=true only after the
    user approves this exact order. Do not resend after a timeout or error: call
    get_open_orders to see whether the order reached IB.
    """
    _require_orders_enabled(confirm)
    order = _build_order(action, quantity, order_type, limit_price, tif, account)
    async with _session(readonly=False) as (ib, messages):
        contract = await _qualify(
            ib, _build_contract(symbol, sec_type, exchange, currency, expiry, con_id)
        )
        trade = ib.placeOrder(contract, order)
        await _wait_while_pending(trade)
        status = trade.orderStatus.status
        rejected = status in REJECTED_STATUSES
        accepted = not rejected and status not in PENDING_STATUSES
        return json.dumps(
            {
                "sent": not rejected,
                "accepted": accepted,
                "rejected": rejected,
                "order": _trade_summary(trade),
                "messages": messages,
            },
            indent=2,
        )


@mcp.tool()
async def cancel_order(order_id: int, confirm: bool = False) -> str:
    """
    Cancel an open order that this bridge placed (same IB_CLIENT_ID), by orderId.

    Refused unless the server runs with IB_ALLOW_ORDERS=1 and confirm is true.
    """
    _require_orders_enabled(confirm)
    async with _session(readonly=False) as (ib, messages):
        # ib_insync asks for this client's open orders while connecting, but only logs a
        # timeout there. Ask again and fail loudly, so a slow reply is not reported as
        # "no such order" while the order is still working.
        try:
            await asyncio.wait_for(ib.reqOpenOrdersAsync(), OPEN_ORDERS_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"IB did not return the open orders within {OPEN_ORDERS_TIMEOUT_SECONDS:g} "
                f"seconds, so order {order_id} was not cancelled and may still be working. "
                "Call get_open_orders to check, then try again."
            ) from e
        trade = next((t for t in ib.openTrades() if t.order.orderId == order_id), None)
        if trade is None:
            raise ValueError(
                f"No open order {order_id} for client id {IB_CLIENT_ID}. "
                "Orders placed by TWS or another client id must be cancelled there."
            )
        ib.cancelOrder(trade.order)
        await _wait_while_pending(trade)
        return json.dumps(
            {"cancelRequested": True, "order": _trade_summary(trade), "messages": messages},
            indent=2,
        )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
