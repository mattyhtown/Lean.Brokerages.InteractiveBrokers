from __future__ import annotations

from unittest.mock import MagicMock

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.ib_client import IBSession


def test_default_settings_disallow_orders():
    settings = Settings()
    assert settings.enable_orders is False
    assert settings.read_only is True
    assert settings.live_trading is False
    assert settings.orders_allowed is False


def test_place_combo_blocked_when_orders_disabled():
    settings = Settings(ENABLE_ORDERS=False, READ_ONLY=True, PAPER=True, LIVE_TRADING=False)
    session = IBSession(settings)
    session._ib = MagicMock()
    assert session.place_combo_order({"short_con_id": 1, "long_con_id": 2}) is None
    session._ib.placeOrder.assert_not_called()


def test_place_combo_submits_when_enabled():
    settings = Settings(ENABLE_ORDERS=True, READ_ONLY=False, PAPER=True, LIVE_TRADING=False)
    session = IBSession(settings)
    session._ib = MagicMock()
    trade = MagicMock()
    trade.order.orderId = 42
    session._ib.placeOrder.return_value = trade
    session._ib.qualifyContracts = MagicMock()
    oid = session.place_combo_order(
        {"short_con_id": 111, "long_con_id": 222, "contracts": 1, "lmt_price": 0.18}
    )
    assert oid == "42"
    session._ib.placeOrder.assert_called_once()