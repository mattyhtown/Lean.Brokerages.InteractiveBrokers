from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.unusual_whales.client import UnusualWhalesClient
from odte_spy_ibkr.unusual_whales.signals import aggregate_flow_bias

logger = logging.getLogger(__name__)


def session_jsonl_path(settings: Settings, day: date) -> Path:
    return Path(settings.uw_session_jsonl_dir) / f"{day.isoformat()}.jsonl"


def daily_cache_path(settings: Settings, day: date) -> Path:
    return Path(settings.uw_backtest_cache_dir) / f"{day.isoformat()}.json"


def append_session_snapshot(settings: Settings, *, bundle: dict, collected_at: datetime | None = None) -> Path:
    """Append one UW poll to per-session JSONL and refresh the day-level replay JSON."""
    collected_at = collected_at or datetime.now(tz=timezone.utc)
    day = date.fromisoformat(str(bundle.get("date", collected_at.date().isoformat())))
    snap = {
        "collected_at": collected_at.isoformat(),
        "bundle": bundle,
    }
    jsonl = session_jsonl_path(settings, day)
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap) + "\n")

    aggregate_bias = aggregate_flow_bias(bundle)
    daily_cache_path(settings, day).parent.mkdir(parents=True, exist_ok=True)
    daily_cache_path(settings, day).write_text(
        json.dumps(
            {
                "date": day.isoformat(),
                "aggregate_bias": aggregate_bias,
                "snapshots": _count_jsonl(jsonl),
                "collected_at": collected_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return jsonl


def collect_once(settings: Settings, *, ticker: str | None = None, day: date | None = None) -> Path:
    client = UnusualWhalesClient(
        settings.unusual_whales_api_key,
        base_url=settings.uw_base_url,
        cache_ttl_sec=0.0,
    )
    if not client.is_configured():
        raise RuntimeError("UNUSUAL_WHALES_API_KEY is not set")
    t = ticker or settings.underlying
    bundle = client.spy_context_bundle(ticker=t, trading_day=day)
    path = append_session_snapshot(settings, bundle=bundle)
    logger.info("UW snapshot appended to %s", path)
    return path


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())