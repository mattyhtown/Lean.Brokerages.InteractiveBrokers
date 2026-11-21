"""Tests for the IB bridge with a stand-in for the IB client; no Gateway needed.

Run: python -m unittest discover -s agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from ib_insync import Contract, OrderState, OrderStatus, Trade

import server


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self, *args) -> None:
        for handler in self.handlers:
            handler(*args)


class FakeIB:
    """Records what the bridge asks IB to do."""

    instances: list["FakeIB"] = []
    active_connections: int = 0
    max_concurrent_connections: int = 0
    connect_delay: float = 0.0
    connect_error: Exception | None = None
    details: list[Contract] = []
    what_if_result: object = None
    place_order_status: str = "Submitted"
    read_error: tuple[int, str] | None = None
    existing_trades: list[Trade] = []
    open_orders_reply: bool = True
    positions: list[Any] = []
    account_summary: list[Any] = []
    tickers: list[Any] = []

    def __init__(self) -> None:
        self.errorEvent = FakeEvent()
        self.connected = False
        self.connect_kwargs: dict = {}
        self.placed: list[tuple[Contract, object]] = []
        self.cancelled: list[object] = []
        self.what_if: list[tuple[Contract, object]] = []
        self.history_kwargs: dict = {}
        self.open_orders_synced = False
        self.market_data_type: int | None = None
        self.summary_account: str | None = None
        FakeIB.instances.append(self)

    async def connectAsync(self, host, port, clientId, readonly=False):
        if FakeIB.connect_error:
            raise FakeIB.connect_error
        self.connect_kwargs = {"host": host, "port": port, "clientId": clientId, "readonly": readonly}
        self.connected = True
        FakeIB.active_connections += 1
        FakeIB.max_concurrent_connections = max(
            FakeIB.max_concurrent_connections, FakeIB.active_connections
        )
        if FakeIB.connect_delay:
            await asyncio.sleep(FakeIB.connect_delay)

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        if self.connected:
            FakeIB.active_connections -= 1
        self.connected = False

    async def qualifyContractsAsync(self, contract):
        self.requested = contract
        return FakeIB.details[:1] if len(FakeIB.details) == 1 else []

    async def reqContractDetailsAsync(self, contract):
        self.requested = contract
        if FakeIB.read_error:
            self.errorEvent.emit(1, FakeIB.read_error[0], FakeIB.read_error[1], contract)
            return []
        return [SimpleNamespace(contract=c) for c in FakeIB.details]

    async def reqHistoricalDataAsync(self, contract, **kwargs):
        self.history_kwargs = kwargs
        if FakeIB.read_error:
            self.errorEvent.emit(1, FakeIB.read_error[0], FakeIB.read_error[1], contract)
            return []
        return [SimpleNamespace(date="2026-11-13", open=1.0, high=2.0, low=0.5, close=1.5, volume=10)]

    async def whatIfOrderAsync(self, contract, order):
        self.what_if.append((contract, order))
        if not isinstance(FakeIB.what_if_result, OrderState):
            self.errorEvent.emit(1, 201, "Order rejected - reason: test", contract)
        return FakeIB.what_if_result

    def placeOrder(self, contract, order):
        order.orderId = 7
        self.placed.append((contract, order))
        if FakeIB.place_order_status in server.REJECTED_STATUSES:
            self.errorEvent.emit(7, 201, "Order rejected - insufficient margin", contract)
        return Trade(
            contract,
            order,
            OrderStatus(orderId=7, status=FakeIB.place_order_status, remaining=order.totalQuantity),
        )

    def openTrades(self):
        """Only knows the orders IB has replayed to this connection."""
        return FakeIB.existing_trades if self.open_orders_synced else []

    async def reqOpenOrdersAsync(self):
        if not FakeIB.open_orders_reply:
            await asyncio.Event().wait()
        self.open_orders_synced = True
        return FakeIB.existing_trades

    async def reqAllOpenOrdersAsync(self):
        if FakeIB.read_error:
            self.errorEvent.emit(0, FakeIB.read_error[0], FakeIB.read_error[1], None)
        return FakeIB.existing_trades

    def cancelOrder(self, order):
        self.cancelled.append(order)

    async def reqPositionsAsync(self):
        if FakeIB.read_error:
            self.errorEvent.emit(0, FakeIB.read_error[0], FakeIB.read_error[1], None)
        return FakeIB.positions

    async def accountSummaryAsync(self, account: str = ""):
        self.summary_account = account
        if FakeIB.read_error:
            self.errorEvent.emit(0, FakeIB.read_error[0], FakeIB.read_error[1], None)
        return FakeIB.account_summary

    def reqMarketDataType(self, marketDataType: int) -> None:
        self.market_data_type = marketDataType

    async def reqTickersAsync(self, *contracts):
        if FakeIB.read_error:
            self.errorEvent.emit(1, FakeIB.read_error[0], FakeIB.read_error[1], contracts[0])
            return []
        return FakeIB.tickers


def eurusd() -> Contract:
    return Contract(secType="CASH", conId=12087792, symbol="EUR", currency="USD", exchange="IDEALPRO", localSymbol="EUR.USD")


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeIB.instances = []
        FakeIB.active_connections = 0
        FakeIB.max_concurrent_connections = 0
        FakeIB.connect_delay = 0.0
        FakeIB.connect_error = None
        FakeIB.details = [eurusd()]
        FakeIB.what_if_result = OrderState(initMarginChange="333.0", commission=2.0, commissionCurrency="USD")
        FakeIB.place_order_status = "Submitted"
        FakeIB.read_error = None
        FakeIB.existing_trades = []
        FakeIB.open_orders_reply = True
        FakeIB.positions = []
        FakeIB.account_summary = []
        FakeIB.tickers = []
        patches = [
            mock.patch.object(server, "IB", FakeIB),
            mock.patch.object(server, "ORDERS_ENABLED", False),
            mock.patch.object(server, "IB_ACCOUNT", ""),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    @property
    def ib(self) -> FakeIB:
        return FakeIB.instances[-1]

    def test_contract_for_forex_pair_splits_symbol(self) -> None:
        contract = server._build_contract("EURUSD", "CASH", "IDEALPRO", "USD")
        self.assertEqual((contract.symbol, contract.currency), ("EUR", "USD"))

    def test_contract_for_future_carries_expiry(self) -> None:
        contract = server._build_contract("ES", "FUT", "CME", "USD", expiry="202612")
        self.assertEqual(contract.lastTradeDateOrContractMonth, "202612")

    def test_contract_by_con_id_ignores_symbol(self) -> None:
        contract = server._build_contract("ignored", "BOND", "SMART", "USD", con_id=123)
        self.assertEqual((contract.conId, contract.symbol), (123, ""))

    def test_order_validation(self) -> None:
        for args in [
            ("HOLD", 1, "MKT", None),
            ("BUY", 0, "MKT", None),
            ("BUY", float("nan"), "MKT", None),
            ("BUY", float("inf"), "MKT", None),
            ("BUY", True, "MKT", None),
            ("BUY", 1, "LMT", None),
            ("BUY", 1, "LMT", 0),
            ("BUY", 1, "LMT", float("nan")),
            ("BUY", 1, "LMT", float("inf")),
            ("BUY", 1, "MKT", 1.05),
            ("BUY", 1, "STP", 1.0),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                server._build_order(*args, tif="DAY", account="")

    async def test_missing_ib_dependency_raises_runtime_error_before_order_build(self) -> None:
        with mock.patch.object(server, "IB", None):
            with self.assertRaisesRegex(RuntimeError, "requirements.txt"):
                await server.preview_order("EURUSD", "BUY", 10, order_type="MKT")
            with mock.patch.object(server, "ORDERS_ENABLED", True):
                with self.assertRaisesRegex(RuntimeError, "requirements.txt"):
                    await server.place_order("EURUSD", "BUY", 10, order_type="MKT", confirm=True)

    def test_ping_reports_orders_disabled(self) -> None:
        self.assertFalse(json.loads(server.ping())["ordersEnabled"])

    async def test_forex_history_uses_midpoint_and_readonly_connection(self) -> None:
        result = json.loads(await server.get_historical_bars("EURUSD", sec_type="CASH", exchange="IDEALPRO"))
        self.assertEqual(result["what_to_show"], "MIDPOINT")
        self.assertEqual(self.ib.history_kwargs["whatToShow"], "MIDPOINT")
        self.assertTrue(self.ib.connect_kwargs["readonly"])
        self.assertEqual(result["bars"][0]["close"], 1.5)
        self.assertFalse(self.ib.connected)

    async def test_ambiguous_contract_is_an_error(self) -> None:
        FakeIB.details = [eurusd(), eurusd()]
        with self.assertRaisesRegex(ValueError, "get_contract_details"):
            await server.get_historical_bars("ES", sec_type="FUT", exchange="CME")

    async def test_connection_failure_is_readable(self) -> None:
        FakeIB.connect_error = ConnectionRefusedError("refused")
        with self.assertRaisesRegex(RuntimeError, "Could not connect to IB at"):
            await server.get_contract_details("EURUSD", sec_type="CASH", exchange="IDEALPRO")

    async def test_preview_does_not_send(self) -> None:
        result = json.loads(
            await server.preview_order("EURUSD", "BUY", 20000, sec_type="CASH", exchange="IDEALPRO", limit_price=1.05)
        )
        self.assertFalse(result["sent"])
        self.assertEqual(result["initMarginChange"], "333.0")
        self.assertEqual(result["commission"], 2.0)
        self.assertEqual(len(self.ib.what_if), 1)
        self.assertEqual(self.ib.placed, [])

    async def test_preview_rejection_carries_ib_message(self) -> None:
        FakeIB.what_if_result = []
        with self.assertRaisesRegex(RuntimeError, "201: Order rejected"):
            await server.preview_order("EURUSD", "BUY", 20000, sec_type="CASH", exchange="IDEALPRO", limit_price=1.05)

    async def test_place_order_refused_when_orders_are_off(self) -> None:
        with self.assertRaisesRegex(PermissionError, "IB_ALLOW_ORDERS=1"):
            await server.place_order("EURUSD", "BUY", 20000, sec_type="CASH", exchange="IDEALPRO", limit_price=1.05, confirm=True)
        self.assertEqual(FakeIB.instances, [])

    async def test_place_order_refused_without_confirm(self) -> None:
        with mock.patch.object(server, "ORDERS_ENABLED", True):
            with self.assertRaisesRegex(PermissionError, "confirm=true"):
                await server.place_order("EURUSD", "BUY", 20000, sec_type="CASH", exchange="IDEALPRO", limit_price=1.05)
        self.assertEqual(FakeIB.instances, [])

    async def test_place_order_sends_once_when_enabled_and_confirmed(self) -> None:
        with mock.patch.object(server, "ORDERS_ENABLED", True), mock.patch.object(server, "IB_ACCOUNT", "DU123"):
            result = json.loads(
                await server.place_order(
                    "EURUSD", "sell", 20000, sec_type="CASH", exchange="IDEALPRO", limit_price=1.05, tif="gtc", confirm=True
                )
            )
        self.assertTrue(result["sent"])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["rejected"])
        self.assertEqual(len(self.ib.placed), 1)
        contract, order = self.ib.placed[0]
        self.assertEqual(contract.conId, 12087792)
        self.assertEqual((order.action, order.totalQuantity, order.orderType, order.lmtPrice, order.tif, order.account),
                         ("SELL", 20000, "LMT", 1.05, "GTC", "DU123"))
        self.assertEqual(result["order"]["status"], "Submitted")
        self.assertEqual(result["order"]["limitPrice"], 1.05)
        self.assertFalse(self.ib.connect_kwargs["readonly"])

    async def test_place_order_reports_rejection(self) -> None:
        FakeIB.place_order_status = "Inactive"
        with mock.patch.object(server, "ORDERS_ENABLED", True):
            result = json.loads(
                await server.place_order(
                    "EURUSD", "BUY", 20000, sec_type="CASH", exchange="IDEALPRO", limit_price=1.05, confirm=True
                )
            )
        self.assertFalse(result["sent"])
        self.assertFalse(result["accepted"])
        self.assertTrue(result["rejected"])
        self.assertEqual(result["order"]["status"], "Inactive")
        self.assertIn("201: Order rejected - insufficient margin", result["messages"])

    async def test_market_preview_reports_null_limit_price(self) -> None:
        result = json.loads(
            await server.preview_order("EURUSD", "BUY", 20000, sec_type="CASH", exchange="IDEALPRO", order_type="MKT")
        )
        self.assertIsNone(result["order"]["limitPrice"])

    async def test_read_tools_surface_ib_error_messages(self) -> None:
        FakeIB.read_error = (162, "No market data permissions")
        hist = json.loads(await server.get_historical_bars("EURUSD", sec_type="CASH", exchange="IDEALPRO"))
        details = json.loads(await server.get_contract_details("EURUSD", sec_type="CASH", exchange="IDEALPRO"))
        orders = json.loads(await server.get_open_orders())
        for payload in (hist, details, orders):
            self.assertIn("162: No market data permissions", payload["messages"])

    async def test_concurrent_sessions_are_serialized(self) -> None:
        FakeIB.connect_delay = 0.03
        await asyncio.gather(
            server.get_open_orders(),
            server.get_contract_details("EURUSD", sec_type="CASH", exchange="IDEALPRO"),
            server.get_historical_bars("EURUSD", sec_type="CASH", exchange="IDEALPRO"),
        )
        self.assertEqual(FakeIB.max_concurrent_connections, 1)
        self.assertEqual(FakeIB.active_connections, 0)

    async def test_cancel_order_needs_flag_and_known_order(self) -> None:
        with self.assertRaises(PermissionError):
            await server.cancel_order(7, confirm=True)
        with mock.patch.object(server, "ORDERS_ENABLED", True):
            with self.assertRaisesRegex(ValueError, "No open order 7"):
                await server.cancel_order(7, confirm=True)
            order = server._build_order("BUY", 1, "MKT", None, "DAY", "")
            order.orderId = 7
            FakeIB.existing_trades = [Trade(eurusd(), order, OrderStatus(orderId=7, status="Submitted"))]
            result = json.loads(await server.cancel_order(7, confirm=True))
        self.assertTrue(result["cancelRequested"])
        self.assertEqual(self.ib.cancelled, [order])
        self.assertIsNone(result["order"]["limitPrice"])

    async def test_cancel_order_reports_when_open_orders_do_not_arrive(self) -> None:
        order = server._build_order("BUY", 1, "MKT", None, "DAY", "")
        order.orderId = 7
        FakeIB.existing_trades = [Trade(eurusd(), order, OrderStatus(orderId=7, status="Submitted"))]
        FakeIB.open_orders_reply = False
        with mock.patch.object(server, "ORDERS_ENABLED", True), \
                mock.patch.object(server, "OPEN_ORDERS_TIMEOUT_SECONDS", 0.05):
            with self.assertRaisesRegex(RuntimeError, "may still be working"):
                await server.cancel_order(7, confirm=True)
        self.assertEqual(self.ib.cancelled, [])
        self.assertFalse(self.ib.connected)

    async def test_open_orders_lists_trades(self) -> None:
        order = server._build_order("BUY", 3, "LMT", 99.5, "DAY", "")
        FakeIB.existing_trades = [Trade(eurusd(), order, OrderStatus(orderId=0, status="PreSubmitted"))]
        result = json.loads(await server.get_open_orders())
        self.assertEqual(result["orders"][0]["status"], "PreSubmitted")
        self.assertEqual(result["orders"][0]["limitPrice"], 99.5)

    async def test_get_positions_filters_by_account(self) -> None:
        FakeIB.positions = [
            SimpleNamespace(account="DU1", contract=eurusd(), position=25000.0, avgCost=1.08),
            SimpleNamespace(account="DU2", contract=eurusd(), position=-10000.0, avgCost=1.09),
        ]
        all_pos = json.loads(await server.get_positions())
        self.assertEqual(len(all_pos["positions"]), 2)
        filtered = json.loads(await server.get_positions(account="DU1"))
        self.assertEqual(len(filtered["positions"]), 1)
        self.assertEqual(filtered["positions"][0]["position"], 25000.0)
        self.assertEqual(filtered["positions"][0]["avgCost"], 1.08)
        self.assertTrue(self.ib.connect_kwargs["readonly"])

    async def test_get_account_summary_filters_key_tags(self) -> None:
        FakeIB.account_summary = [
            SimpleNamespace(account="DU1", tag="NetLiquidation", value="100500.25", currency="USD"),
            SimpleNamespace(account="DU1", tag="BuyingPower", value="402001.00", currency="USD"),
            SimpleNamespace(account="DU1", tag="IgnoredCustomTag", value="999", currency="USD"),
        ]
        with mock.patch.object(server, "IB_ACCOUNT", "DU1"):
            result = json.loads(await server.get_account_summary())
        self.assertEqual(self.ib.summary_account, "DU1")
        self.assertEqual(result["summary"]["DU1"]["NetLiquidation"]["value"], "100500.25")
        self.assertIn("BuyingPower", result["summary"]["DU1"])
        self.assertNotIn("IgnoredCustomTag", result["summary"]["DU1"])
        self.assertTrue(self.ib.connect_kwargs["readonly"])

    async def test_get_quote_strips_nan_and_computes_midpoint(self) -> None:
        FakeIB.tickers = [
            SimpleNamespace(
                bid=1.0840,
                ask=1.0842,
                last=float("nan"),
                close=1.0835,
                high=float("nan"),
                low=1.0810,
                volume=float("nan"),
                bidSize=1000000.0,
                askSize=500000.0,
            )
        ]
        result = json.loads(await server.get_quote("EURUSD", sec_type="CASH", exchange="IDEALPRO"))
        self.assertEqual(self.ib.market_data_type, 3)
        self.assertEqual(result["bid"], 1.0840)
        self.assertEqual(result["ask"], 1.0842)
        self.assertEqual(result["midpoint"], 1.0841)
        self.assertIsNone(result["last"])
        self.assertIsNone(result["high"])
        self.assertIsNone(result["volume"])
        self.assertEqual(result["close"], 1.0835)
        self.assertTrue(self.ib.connect_kwargs["readonly"])

    async def test_get_quote_rejects_invalid_market_data_type(self) -> None:
        for bad in (0, 5, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                await server.get_quote("EURUSD", sec_type="CASH", exchange="IDEALPRO", market_data_type=bad)
        self.assertEqual(FakeIB.instances, [])


if __name__ == "__main__":
    unittest.main()
