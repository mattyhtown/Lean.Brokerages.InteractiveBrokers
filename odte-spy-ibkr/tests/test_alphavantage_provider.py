from __future__ import annotations

import pytest

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import MarketContext, ProviderNotConfigured
from odte_spy_ibkr.providers.alphavantage import AlphaVantageProvider


def test_alphavantage_provider_missing_key():
    settings = Settings(PROVIDERS_ENABLED="alphavantage", ALPHAVANTAGE_API_KEY="")
    provider = AlphaVantageProvider(settings)
    assert not provider.is_configured()
    with pytest.raises(ProviderNotConfigured):
        provider._fetch(MarketContext(underlying="SPY", spot=500.0))


def test_alphavantage_provider_quote(monkeypatch):
    settings = Settings(PROVIDERS_ENABLED="alphavantage", ALPHAVANTAGE_API_KEY="secret-not-logged")
    provider = AlphaVantageProvider(settings)
    monkeypatch.setattr(
        "odte_spy_ibkr.providers.alphavantage.fetch_global_quote",
        lambda **kwargs: 501.25,
    )
    delta = provider._fetch(MarketContext(underlying="SPY"))
    assert delta.spot == 501.25
    assert delta.provider_id == "alphavantage"