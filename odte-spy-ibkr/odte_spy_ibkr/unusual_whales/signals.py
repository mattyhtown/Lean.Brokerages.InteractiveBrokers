from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable


class FlowBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class FlowSignal:
    bias: FlowBias
    strength: float
    as_of: datetime
    notional: float | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _clamp_strength(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_timestamp(item: dict[str, Any]) -> datetime:
    for key in ("created_at", "timestamp", "executed_at", "date", "as_of"):
        raw = item.get(key)
        if not raw:
            continue
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        text = str(raw).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            continue
    return datetime.now(tz=timezone.utc)


def _item_premium(item: dict[str, Any]) -> float:
    for key in ("premium", "total_premium", "notional", "size"):
        val = item.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _item_side_bias(item: dict[str, Any]) -> tuple[FlowBias, float]:
    option_type = str(item.get("option_type") or item.get("put_call") or item.get("type") or "").upper()
    sentiment = str(item.get("sentiment") or item.get("bias") or "").lower()
    side = str(item.get("side") or item.get("aggressor") or "").lower()

    bias = FlowBias.NEUTRAL
    if sentiment in {"bullish", "bull"}:
        bias = FlowBias.BULLISH
    elif sentiment in {"bearish", "bear"}:
        bias = FlowBias.BEARISH
    elif option_type in {"C", "CALL"}:
        bias = FlowBias.BULLISH if "ask" in side or side == "buy" else FlowBias.NEUTRAL
    elif option_type in {"P", "PUT"}:
        bias = FlowBias.BEARISH if "ask" in side or side == "buy" else FlowBias.NEUTRAL

    premium = _item_premium(item)
    strength = _clamp_strength(premium / 250_000.0) if premium else 0.25
    return bias, strength


def signals_from_payload(payload: Any) -> list[FlowSignal]:
    if payload is None:
        return []
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("results") or payload.get("flows") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        return []

    out: list[FlowSignal] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        bias, strength = _item_side_bias(item)
        out.append(
            FlowSignal(
                bias=bias,
                strength=strength,
                as_of=_parse_timestamp(item),
                notional=_item_premium(item) or None,
                tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
                raw=item,
            )
        )
    return out


def aggregate_flow_signals(signals: Iterable[FlowSignal]) -> FlowSignal:
    signals = list(signals)
    if not signals:
        return FlowSignal(
            bias=FlowBias.NEUTRAL,
            strength=0.0,
            as_of=datetime.now(tz=timezone.utc),
        )

    score = 0.0
    weight = 0.0
    latest = max(s.as_of for s in signals)
    tags: list[str] = []
    raw_samples: list[dict[str, Any]] = []

    for s in signals:
        w = max(0.05, s.strength)
        if s.bias == FlowBias.BULLISH:
            score += w
        elif s.bias == FlowBias.BEARISH:
            score -= w
        weight += w
        tags.extend(s.tags)
        if len(raw_samples) < 5:
            raw_samples.append(s.raw)

    norm = score / weight if weight else 0.0
    if norm > 0.12:
        bias = FlowBias.BULLISH
    elif norm < -0.12:
        bias = FlowBias.BEARISH
    else:
        bias = FlowBias.NEUTRAL

    return FlowSignal(
        bias=bias,
        strength=_clamp_strength(abs(norm)),
        as_of=latest,
        tags=sorted(set(tags))[:20],
        raw={"samples": raw_samples, "signal_count": len(signals)},
    )