from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from odte_spy_ibkr.chain.legs import OptionCandidate


@dataclass(frozen=True)
class ChainSkewSnapshot:
    as_of: datetime
    expiry: date
    call_volume: float
    put_volume: float
    call_oi: float
    put_oi: float
    call_premium_mid: float
    put_premium_mid: float
    net_call_bias: float
    delta_bucket_call: float
    delta_bucket_put: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "as_of": self.as_of.isoformat(),
            "expiry": self.expiry.isoformat(),
            "call_volume": self.call_volume,
            "put_volume": self.put_volume,
            "call_oi": self.call_oi,
            "put_oi": self.put_oi,
            "call_premium_mid": self.call_premium_mid,
            "put_premium_mid": self.put_premium_mid,
            "net_call_bias": self.net_call_bias,
            "delta_bucket_call": self.delta_bucket_call,
            "delta_bucket_put": self.delta_bucket_put,
        }


def _weight(candidate: OptionCandidate, *, prefer_oi: bool) -> float:
    if prefer_oi and candidate.open_interest and candidate.open_interest > 0:
        return float(candidate.open_interest)
    if candidate.volume and candidate.volume > 0:
        return float(candidate.volume)
    return 1.0


def _side_metrics(rows: Sequence[OptionCandidate], *, prefer_oi: bool) -> tuple[float, float, float, float]:
    volume = 0.0
    oi = 0.0
    premium = 0.0
    delta_bucket = 0.0
    for c in rows:
        w = _weight(c, prefer_oi=prefer_oi)
        volume += float(c.volume or 0)
        oi += float(c.open_interest or 0)
        mid = c.mid or 0.0
        premium += mid * w
        if c.delta is not None:
            delta_bucket += abs(c.delta) * w
    return volume, oi, premium, delta_bucket


def build_chain_skew(
    candidates: Sequence[OptionCandidate],
    *,
    expiry: date,
    spot: float | None = None,
    as_of: datetime | None = None,
    prefer_oi: bool = True,
) -> ChainSkewSnapshot:
    """Aggregate call vs put pressure on the 0DTE chain."""
    rows = [c for c in candidates if c.expiry == expiry]
    calls = [c for c in rows if c.right == "C"]
    puts = [c for c in rows if c.right == "P"]

    cv, coi, cprem, cdelta = _side_metrics(calls, prefer_oi=prefer_oi)
    pv, poi, pprem, pdelta = _side_metrics(puts, prefer_oi=prefer_oi)

    numerators = [cprem, cv, coi]
    denominators = [pprem, pv, poi]
    scores: list[float] = []
    for num, den in zip(numerators, denominators, strict=False):
        total = num + den
        if total > 0:
            scores.append((num - den) / total)
    net_call_bias = sum(scores) / len(scores) if scores else 0.0
    net_call_bias = max(-1.0, min(1.0, net_call_bias))

    if spot is not None and net_call_bias == 0.0 and rows:
        # Mild spot-based tie-break when marks are sparse (common in backtest fixtures).
        atm = min(rows, key=lambda c: abs(c.strike - spot))
        net_call_bias = 0.1 if atm.right == "C" else -0.1

    return ChainSkewSnapshot(
        as_of=as_of or datetime.now(),
        expiry=expiry,
        call_volume=cv,
        put_volume=pv,
        call_oi=coi,
        put_oi=poi,
        call_premium_mid=cprem,
        put_premium_mid=pprem,
        net_call_bias=net_call_bias,
        delta_bucket_call=cdelta,
        delta_bucket_put=pdelta,
    )