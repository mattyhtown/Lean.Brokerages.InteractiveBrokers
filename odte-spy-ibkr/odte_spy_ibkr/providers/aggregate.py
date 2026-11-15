from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from odte_spy_ibkr.providers.base import (
    BaseProvider,
    FlowBias,
    MarketContext,
    MarketContextDelta,
    ProviderNotConfigured,
    VolRegime,
)

logger = logging.getLogger(__name__)


def _bias_score(bias: FlowBias) -> float:
    if bias == FlowBias.BULLISH:
        return 1.0
    if bias == FlowBias.BEARISH:
        return -1.0
    return 0.0


def _score_to_bias(score: float) -> FlowBias:
    if score > 0.15:
        return FlowBias.BULLISH
    if score < -0.15:
        return FlowBias.BEARISH
    return FlowBias.NEUTRAL


class MarketContextAggregator:
    def __init__(
        self,
        providers: Iterable[BaseProvider],
        *,
        default_weights: dict[str, dict[str, float]] | None = None,
        underlying: str = "SPY",
    ) -> None:
        self.providers = list(providers)
        self.underlying = underlying
        self.default_weights = default_weights or {
            "ibkr": {"spot": 1.0},
            "alpaca": {"spot": 0.5},
            "unusual_whales": {"flow": 1.0},
            "coinbase": {"macro": 1.0},
            "orats": {"vol": 0.8},
        }

    def build(self, base: MarketContext | None = None) -> MarketContext:
        ctx = base or MarketContext(underlying=self.underlying)
        return merge_deltas(ctx, self.fetch_deltas(ctx), self.default_weights)

    def fetch_deltas(self, ctx: MarketContext) -> list[MarketContextDelta]:
        """One delta per provider that answered or failed; unconfigured ones are skipped."""
        deltas: list[MarketContextDelta] = []

        for provider in self.providers:
            try:
                delta = provider.fetch(ctx)
                deltas.append(delta)
            except ProviderNotConfigured as exc:
                logger.info("skip provider %s: %s", provider.provider_id, exc)
            except Exception as exc:  # noqa: BLE001 — isolate provider faults
                logger.warning("provider %s failed: %s", provider.provider_id, exc)
                deltas.append(
                    MarketContextDelta(
                        provider_id=provider.provider_id,
                        error=str(exc),
                        note="fetch_failed",
                    )
                )

        return deltas

    def poll_provider_safe(self, provider_id: str, ctx: MarketContext) -> MarketContextDelta | None:
        for p in self.providers:
            if p.provider_id == provider_id:
                try:
                    return p.fetch(ctx)
                except ProviderNotConfigured:
                    return None
        return None


def merge_deltas(
    seed: MarketContext,
    deltas: list[MarketContextDelta],
    default_weights: dict[str, dict[str, float]],
) -> MarketContext:
    spot_num = 0.0
    spot_den = 0.0
    vix_num = 0.0
    vix_den = 0.0
    flow_score = 0.0
    flow_weight = 0.0
    vol_scores: list[tuple[VolRegime, float]] = []
    macro_flags: list[bool] = []
    bias_num = 0.0
    bias_den = 0.0
    headline_scores: list[float] = []
    blackout_flags: list[bool] = []
    chain_skew = seed.chain_skew
    latest_as_of: datetime | None = seed.as_of
    notes = dict(seed.provider_notes)
    raw = dict(seed.raw_by_provider)

    for d in deltas:
        wconf = default_weights.get(d.provider_id, {})
        if d.note:
            notes[d.provider_id] = d.note
        if d.error:
            notes[d.provider_id] = f"error: {d.error}"
        if d.raw:
            raw[d.provider_id] = d.raw

        if d.spot is not None:
            w = d.spot_weight or wconf.get("spot", 0.0)
            if w > 0:
                spot_num += d.spot * w
                spot_den += w
        if d.vix is not None:
            w = d.vix_weight or wconf.get("vix", 0.5)
            if w > 0:
                vix_num += d.vix * w
                vix_den += w
        if d.flow_bias is not None and d.flow_strength is not None:
            w = d.flow_weight or wconf.get("flow", 0.0)
            if w > 0:
                flow_score += _bias_score(d.flow_bias) * d.flow_strength * w
                flow_weight += w
        if d.vol_regime is not None:
            w = d.vol_weight or wconf.get("vol", 0.5)
            vol_scores.append((d.vol_regime, w))
        if d.macro_risk_off is not None:
            macro_flags.append(d.macro_risk_off)
        if d.net_call_bias is not None:
            w = d.net_call_bias_weight or wconf.get("chain_skew", 0.0)
            if w > 0:
                bias_num += d.net_call_bias * w
                bias_den += w
        if d.chain_skew is not None:
            chain_skew = d.chain_skew
        if d.macro_headline_score is not None:
            headline_scores.append(d.macro_headline_score)
        if d.macro_event_blackout is not None:
            blackout_flags.append(d.macro_event_blackout)
        if d.as_of is not None:
            if latest_as_of is None or d.as_of > latest_as_of:
                latest_as_of = d.as_of

    spot = seed.spot if spot_den == 0 else spot_num / spot_den
    if spot_den == 0 and seed.spot is not None:
        spot = seed.spot

    vix = seed.vix
    if vix_den > 0:
        vix = vix_num / vix_den

    if flow_weight > 0:
        norm = flow_score / flow_weight
        bias = _score_to_bias(norm)
        strength = min(1.0, abs(norm))
    else:
        bias, strength = seed.flow_bias, seed.flow_strength

    vol_regime = seed.vol_regime
    if vol_scores:
        vol_regime = max(vol_scores, key=lambda x: x[1])[0]

    macro = seed.macro_risk_off
    if macro_flags:
        macro = any(macro_flags)

    net_call_bias = seed.net_call_bias
    if bias_den > 0:
        net_call_bias = bias_num / bias_den
    elif chain_skew is not None:
        net_call_bias = chain_skew.net_call_bias

    macro_headline = seed.macro_headline_score
    if headline_scores:
        macro_headline = max(headline_scores, key=abs)

    macro_blackout = seed.macro_event_blackout
    if blackout_flags:
        macro_blackout = any(blackout_flags)

    return MarketContext(
        underlying=seed.underlying,
        spot=spot,
        vix=vix,
        flow_bias=bias,
        flow_strength=strength,
        vol_regime=vol_regime,
        macro_risk_off=macro,
        net_call_bias=net_call_bias,
        net_call_risk=seed.net_call_risk,
        chain_skew=chain_skew,
        macro_headline_score=macro_headline,
        macro_event_blackout=macro_blackout,
        as_of=latest_as_of or datetime.now(tz=timezone.utc),
        provider_notes=notes,
        raw_by_provider=raw,
    )