from __future__ import annotations

from datetime import datetime, timezone

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, FlowBias, MarketContext, MarketContextDelta
from odte_spy_ibkr.unusual_whales.client import UnusualWhalesClient
from odte_spy_ibkr.unusual_whales.signals import FlowBias as UWFlowBias
from odte_spy_ibkr.unusual_whales.signals import aggregate_flow_signals, signals_from_payload


class UnusualWhalesProvider(BaseProvider):
    provider_id = "unusual_whales"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled="unusual_whales" in settings.providers_enabled_set)
        self._settings = settings
        self._client = UnusualWhalesClient(
            settings.unusual_whales_api_key,
            base_url=settings.uw_base_url,
            cache_ttl_sec=settings.uw_cache_ttl_sec,
        )

    def is_configured(self) -> bool:
        return self._client.is_configured()

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        ticker = ctx.underlying if ctx else self._settings.underlying
        bundle = self._client.spy_context_bundle(ticker)
        signals = []
        for key in ("flow_recent", "flow_alerts", "unusual"):
            signals.extend(signals_from_payload(bundle.get(key)))

        agg = aggregate_flow_signals(signals)
        bias_map = {
            UWFlowBias.BULLISH: FlowBias.BULLISH,
            UWFlowBias.BEARISH: FlowBias.BEARISH,
            UWFlowBias.NEUTRAL: FlowBias.NEUTRAL,
        }
        return MarketContextDelta(
            provider_id=self.provider_id,
            flow_bias=bias_map[agg.bias],
            flow_strength=agg.strength,
            flow_weight=1.0,
            as_of=agg.as_of or datetime.now(tz=timezone.utc),
            note=f"signals={len(signals)}",
            raw=bundle,
        )