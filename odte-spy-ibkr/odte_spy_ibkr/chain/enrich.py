from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from odte_spy_ibkr.chain.bs_greeks import default_signal_as_of, delta_from_mid


def enrich_chain_rows(
    rows: list[dict[str, Any]],
    *,
    day: date,
    spot: float,
    as_of: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Fill null delta from same-timestamp spot, mid price, and 0DTE expiry (no lookahead).
    Rows with existing delta are left unchanged.
    """
    as_of = as_of or default_signal_as_of(day)
    stats = {"computed": 0, "failed": 0, "skipped_existing": 0}
    out: list[dict[str, Any]] = []
    if spot <= 0:
        stats["failed"] = len(rows)
        return [dict(r) for r in rows], stats

    for row in rows:
        row = dict(row)
        if row.get("delta") is not None:
            stats["skipped_existing"] += 1
            out.append(row)
            continue
        bid = row.get("bid")
        ask = row.get("ask")
        if bid is None or ask is None:
            stats["failed"] += 1
            out.append(row)
            continue
        bid_f, ask_f = float(bid), float(ask)
        if bid_f <= 0 and ask_f <= 0:
            stats["failed"] += 1
            out.append(row)
            continue
        mid = (bid_f + ask_f) / 2.0
        right = str(row.get("right", "")).upper()[:1]
        if right not in {"P", "C"}:
            stats["failed"] += 1
            out.append(row)
            continue
        strike = float(row["strike"])
        delta = delta_from_mid(
            mid=mid,
            spot=spot,
            strike=strike,
            right=right,  # type: ignore[arg-type]
            as_of=as_of,
            day=day,
        )
        if delta is None:
            stats["failed"] += 1
            row["delta"] = None
        else:
            stats["computed"] += 1
            row["delta"] = delta
        out.append(row)
    return out, stats


def enrich_chain_row_at_bar(
    row: dict[str, Any],
    *,
    day: date,
    spot: float,
    bar_as_of: datetime,
) -> dict[str, Any]:
    """Single-row enrich using the option bar's timestamp (ingest path)."""
    enriched, stats = enrich_chain_rows([row], day=day, spot=spot, as_of=bar_as_of)
    return enriched[0] if enriched else row