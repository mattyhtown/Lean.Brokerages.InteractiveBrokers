from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from odte_spy_ibkr.chain.skew import ChainSkewSnapshot


class FlowBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class VolRegime(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass
class MarketContext:
    """Merged view consumed by strategy and risk gates."""

    underlying: str = "SPY"
    spot: float | None = None
    vix: float | None = None
    flow_bias: FlowBias = FlowBias.NEUTRAL
    flow_strength: float = 0.0
    vol_regime: VolRegime = VolRegime.UNKNOWN
    macro_risk_off: bool = False
    net_call_bias: float = 0.0
    net_call_risk: float = 0.0
    chain_skew: ChainSkewSnapshot | None = None
    macro_headline_score: float = 0.0
    macro_event_blackout: bool = False
    as_of: datetime | None = None
    provider_notes: dict[str, str] = field(default_factory=dict)
    raw_by_provider: dict[str, dict[str, Any]] = field(default_factory=dict)

    def effective_bias_strength(self) -> tuple[FlowBias, float]:
        return self.flow_bias, self.flow_strength


@dataclass
class MarketContextDelta:
    """Partial update from a single provider."""

    provider_id: str
    spot: float | None = None
    spot_weight: float = 0.0
    vix: float | None = None
    vix_weight: float = 0.0
    flow_bias: FlowBias | None = None
    flow_strength: float | None = None
    flow_weight: float = 0.0
    vol_regime: VolRegime | None = None
    vol_weight: float = 0.0
    macro_risk_off: bool | None = None
    net_call_bias: float | None = None
    net_call_bias_weight: float = 0.0
    macro_headline_score: float | None = None
    macro_event_blackout: bool | None = None
    chain_skew: Any | None = None
    as_of: datetime | None = None
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ProviderError(Exception):
    def __init__(self, provider_id: str, message: str) -> None:
        self.provider_id = provider_id
        super().__init__(message)


class ProviderNotConfigured(ProviderError):
    """Provider disabled or missing credentials."""


class BaseProvider(ABC):
    provider_id: str

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    def fetch(self, ctx: MarketContext | None = None) -> MarketContextDelta:
        if not self.enabled:
            return MarketContextDelta(provider_id=self.provider_id, note="disabled")
        if not self.is_configured():
            raise ProviderNotConfigured(self.provider_id, f"{self.provider_id} is not configured")
        return self._fetch(ctx)

    @abstractmethod
    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        ...