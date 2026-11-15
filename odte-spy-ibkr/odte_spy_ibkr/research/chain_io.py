from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from odte_spy_ibkr.chain import OptionCandidate, today_expiry
from odte_spy_ibkr.research.paths import CHAIN_ROOT, chain_path


def save_chain_snapshot(
    rows: list[dict[str, Any]],
    day: date,
    *,
    snapshot: str = "signal",
    source: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    path = chain_path(day, snapshot=snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "date": day.isoformat(),
        "source": source,
        "snapshot": snapshot,
        "rows": rows,
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_chain_rows(day: date, *, snapshot: str = "signal", root: Path | None = None) -> list[dict[str, Any]]:
    base = root or CHAIN_ROOT
    path = base / f"{day.isoformat()}.json" if snapshot == "signal" else base / f"{day.isoformat()}_{snapshot}.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    return list(raw.get("rows") or [])


def rows_to_candidates(rows: list[dict[str, Any]], day: date) -> list[OptionCandidate]:
    expiry = today_expiry(day)
    out: list[OptionCandidate] = []
    for row in rows:
        right = str(row.get("right", "")).upper()[:1]
        if right not in {"C", "P"}:
            continue
        out.append(
            OptionCandidate(
                symbol=str(row.get("symbol", "")),
                strike=float(row["strike"]),
                right=right,  # type: ignore[arg-type]
                expiry=expiry,
                delta=float(row["delta"]) if row.get("delta") is not None else None,
                bid=float(row["bid"]) if row.get("bid") is not None else None,
                ask=float(row["ask"]) if row.get("ask") is not None else None,
                volume=float(row.get("volume") or 0),
                open_interest=float(row.get("open_interest") or 0),
            )
        )
    return out