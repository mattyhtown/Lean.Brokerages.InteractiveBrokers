from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta

if TYPE_CHECKING:
    from odte_spy_ibkr.ib_client import IBSession


class IBKRProvider(BaseProvider):
    provider_id = "ibkr"

    def __init__(self, session: IBSession | None, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self._session = session

    def is_configured(self) -> bool:
        return self._session is not None and self._session.is_connected()

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        assert self._session is not None
        symbol = ctx.underlying if ctx else self._session.underlying
        spot = self._session.get_spot_price(symbol)
        return MarketContextDelta(
            provider_id=self.provider_id,
            spot=spot,
            spot_weight=1.0,
            as_of=datetime.now(tz=timezone.utc),
            note="ibkr_spot",
            raw={"spot": spot, "symbol": symbol},
        )