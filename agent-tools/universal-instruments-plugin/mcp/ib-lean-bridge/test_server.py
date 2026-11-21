"""Tests for the IB bridge with a stand-in for the IB client; no Gateway needed.

Run: python -m unittest discover -s agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
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
    connect_error: Exception | None = None
    details: list[Contract] = []
    what_if_result: object = None
    existing_trades: list[Trade] = []
    open_orders_reply: bool = True

    def __init__(self) -> None:
        self.errorEvent = FakeEvent()
        self.connected = False
        self.connect_kwargs: dict = {}
        self.placed: list[tuple[Contract, object]] = []
        self.cancelled: list[object] = []
        self.what_if: list[tuple[Contract, object]] = []
        self.history_kwargs: dict = {}
        self.open_orders_synced = False
        FakeIB.instances.append(self)

    async def connectAsync(self, host, port, clientId, readonly=False):
        if FakeIB.connect_error:
            raise FakeIB.connect_error
        self.connect_kwargs = {"host": host, "port": port, "clientId": clientId, "readonly": readonly}
        self.connected = True

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        self.connected = False

    async def qualifyContractsAsync(self, contract):
        self.requested = contract
        return FakeIB.details[:1] if len(FakeIB.details) == 1 else []

    async def reqContractDetailsAsync(self, contract):
        self.requested = contract
        return [SimpleNamespace(contract=c) for c in FakeIB.details]

    async def reqHistoricalDataAsync(self, contract, **kwargs):
        self.history_kwargs = kwargs
        return [SimpleNamespace(date="2026-11-13", open=1.0, high=2.0, low=0.5, close=1.5, volume=10)]

    async def whatIfOrderAsync(self, contract, order):
        self.what_if.append((contract, order))
        if not isinstance(FakeIB.what_if_result, OrderState):
            self.errorEvent.emit(1, 201, "Order rejected - reason: test", contract)
        return FakeIB.what_if_result

    def placeOrder(self, contract, order):
        order.orderId = 7
        self.placed.append((contract, order))
        return Trade(contract, order, OrderStatus(orderId=7, status="Submitted", remaining=order.totalQuantity))

    def openTrades(self):
        """Only knows the orders IB has replayed to this connection."""
        return FakeIB.existing_trades if self.open_orders_synced else []

    async def reqOpenOrdersAsync(self):
        if not FakeIB.open_orders_reply:
            await asyncio.Event().wait()
        self.open_orders_synced = True
        return FakeIB.existing_trades

    async def reqAllOpenOrdersAsync(self):
        return FakeIB.existing_trades

    def cancelOrder(self, order):
        self.cancelled.append(order)


def eurusd() -> Contract:
    return Contract(secType="CASH", conId=12087792, symbol="EUR", currency="USD", exchange="IDEALPRO", localSymbol="EUR.USD")


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeIB.instances = []
        FakeIB.connect_error = None
        FakeIB.details = [eurusd()]
        FakeIB.what_if_result = OrderState(initMarginChange="333.0", commission=2.0, commissionCurrency="USD")
        FakeIB.existing_trades = []
        FakeIB.open_orders_reply = True
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
            ("BUY", 1, "LMT", None),
            ("BUY", 1, "LMT", 0),
            ("BUY", 1, "STP", 1.0),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                server._build_order(*args, tif="DAY", account="")

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
        self.assertEqual(len(self.ib.placed), 1)
        contract, order = self.ib.placed[0]
        self.assertEqual(contract.conId, 12087792)
        self.assertEqual((order.action, order.totalQuantity, order.orderType, order.lmtPrice, order.tif, order.account),
                         ("SELL", 20000, "LMT", 1.05, "GTC", "DU123"))
        self.assertEqual(result["order"]["status"], "Submitted")
        self.assertEqual(result["order"]["limitPrice"], 1.05)
        self.assertFalse(self.ib.connect_kwargs["readonly"])

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


if __name__ == "__main__":
    unittest.main()
