from __future__ import annotations

import pytest

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import MarketContext, ProviderNotConfigured
from odte_spy_ibkr.providers.openbb import OpenBBProvider


def test_openbb_provider_not_configured_without_package(monkeypatch):
    settings = Settings(PROVIDERS_ENABLED="openbb")
    provider = OpenBBProvider(settings)
    monkeypatch.setattr("odte_spy_ibkr.providers.openbb.openbb_available", lambda: False)
    assert not provider.is_configured()
    with pytest.raises(ProviderNotConfigured):
        provider._fetch(MarketContext(underlying="SPY", spot=500.0))


def test_openbb_provider_returns_skew_delta(monkeypatch):
    settings = Settings(PROVIDERS_ENABLED="openbb", OPENBB_LIVE_PROVIDER="cboe")
    provider = OpenBBProvider(settings)
    rows = [
        {
            "symbol": "SPY260702P00740000",
            "strike": 740.0,
            "right": "P",
            "bid": 2.0,
            "ask": 2.1,
            "delta": -0.15,
            "volume": 10.0,
            "open_interest": 100.0,
        }
    ]

    monkeypatch.setattr("odte_spy_ibkr.providers.openbb.openbb_available", lambda: True)
    monkeypatch.setattr(
        "odte_spy_ibkr.providers.openbb.fetch_option_chains",
        lambda **kwargs: rows,
    )
    monkeypatch.setattr("odte_spy_ibkr.providers.openbb.configure_openbb_session", lambda pat: None)
    delta = provider._fetch(MarketContext(underlying="SPY", spot=745.0))
    assert delta.provider_id == "openbb"
    assert delta.net_call_bias is not None