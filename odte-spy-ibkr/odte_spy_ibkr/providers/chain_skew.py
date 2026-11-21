from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from odte_spy_ibkr.chain import OptionCandidate, build_chain_skew, today_expiry
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta


class ChainSkewProvider(BaseProvider):
    """Builds chain skew from cached option candidates attached to the session."""

    provider_id = "chain_skew"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled=True)
        self._settings = settings
        self._candidates: list[OptionCandidate] = []

    def set_candidates(self, candidates: Sequence[OptionCandidate]) -> None:
        self._candidates = list(candidates)

    def is_configured(self) -> bool:
        return bool(self._candidates)

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        spot = ctx.spot if ctx else None
        expiry = today_expiry()
        skew = build_chain_skew(
            self._candidates,
            expiry=expiry,
            spot=spot,
            as_of=datetime.now(tz=timezone.utc),
        )
        return MarketContextDelta(
            provider_id=self.provider_id,
            net_call_bias=skew.net_call_bias,
            net_call_bias_weight=1.0,
            chain_skew=skew,
            as_of=skew.as_of,
            note="chain_skew",
            raw=skew.to_dict(),
        )