from __future__ import annotations

from typing import TYPE_CHECKING

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.alpaca import AlpacaProvider
from odte_spy_ibkr.providers.base import BaseProvider
from odte_spy_ibkr.providers.coinbase import CoinbaseProvider
from odte_spy_ibkr.providers.ibkr import IBKRProvider
from odte_spy_ibkr.providers.macro_news import MacroNewsProvider
from odte_spy_ibkr.providers.tradingeconomics_macro import TradingEconomicsMacroProvider
from odte_spy_ibkr.providers.stubs import build_stub_providers
from odte_spy_ibkr.providers.unusual_whales import UnusualWhalesProvider

if TYPE_CHECKING:
    from odte_spy_ibkr.ib_client import IBSession


def build_providers(settings: Settings, ib_session: IBSession | None) -> list[BaseProvider]:
    enabled = settings.providers_enabled_set
    providers: list[BaseProvider] = [
        IBKRProvider(ib_session, enabled="ibkr" in enabled),
        UnusualWhalesProvider(settings),
        AlpacaProvider(settings),
        CoinbaseProvider(settings),
        MacroNewsProvider(settings),
        TradingEconomicsMacroProvider(settings),
        *build_stub_providers(enabled),
    ]
    order = {pid: i for i, pid in enumerate(settings.providers_enabled_list)}
    return sorted(providers, key=lambda p: order.get(p.provider_id, 999))