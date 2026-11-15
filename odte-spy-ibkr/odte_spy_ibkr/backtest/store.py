"""Row-oriented cache IO: parquet when pandas/pyarrow are installed, JSON otherwise."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:  # optional `backtest` extra
    import pandas as pd
except ImportError:  # pragma: no cover - exercised only without the extra
    pd = None

logger = logging.getLogger(__name__)


def read_rows(parquet_path: Path) -> list[dict[str, Any]] | None:
    """Rows from `parquet_path` or its `.json` sibling; None when neither is readable."""
    json_path = parquet_path.with_suffix(".json")
    if parquet_path.is_file() and pd is not None:
        try:
            frame = pd.read_parquet(parquet_path)
            return json.loads(frame.to_json(orient="records", date_format="iso"))
        except (OSError, ValueError) as exc:
            logger.warning("unreadable cache file %s: %s", parquet_path, exc)
    if json_path.is_file():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("rows") or [raw]
        return list(raw)
    return None


def write_rows(parquet_path: Path, rows: list[dict[str, Any]]) -> Path:
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    if pd is not None:
        try:
            pd.DataFrame(rows).to_parquet(parquet_path, index=False)
            return parquet_path
        except (ImportError, ValueError, OSError) as exc:
            logger.warning("parquet write failed for %s (%s); using JSON", parquet_path, exc)
    json_path = parquet_path.with_suffix(".json")
    json_path.write_text(json.dumps(rows), encoding="utf-8")
    return json_path
