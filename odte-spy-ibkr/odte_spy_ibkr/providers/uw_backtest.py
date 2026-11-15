from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from odte_spy_ibkr.config import Settings, UWBacktestMode
from odte_spy_ibkr.providers.base import FlowBias
from odte_spy_ibkr.unusual_whales.signals import aggregate_flow_bias


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UwSnapshot:
    """One cached flow reading and the time it was captured."""

    collected_at: datetime
    bias: float


def _parse_collected_at(raw: object) -> datetime | None:
    if not raw:
        return None
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class UwBacktestReplay:
    """Replay Unusual Whales flow from on-disk cache (no live API in backtest).

    Timestamped session snapshots (`UW_SESSION_JSONL_DIR/{date}.jsonl`) are preferred over
    the day-level file (`UW_BACKTEST_CACHE_DIR/{date}.json`) because they can be replayed
    without lookahead.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dir = Path(settings.uw_backtest_cache_dir)
        self._by_day: dict[date, list[UwSnapshot] | None] = {}

    def load_snapshots(self, day: date) -> list[UwSnapshot] | None:
        """Snapshots in time order; None when the day has no UW cache at all."""
        if day not in self._by_day:
            self._by_day[day] = self._read_snapshots(day)
        return self._by_day[day]

    def _read_snapshots(self, day: date) -> list[UwSnapshot] | None:
        jsonl = Path(self._settings.uw_session_jsonl_dir) / f"{day.isoformat()}.jsonl"
        if jsonl.is_file():
            snaps = self._read_jsonl(jsonl)
            if snaps:
                return snaps
        path = self._dir / f"{day.isoformat()}.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            collected_at = _parse_collected_at(payload.get("collected_at"))
            if collected_at is None:
                # A reading with no capture time cannot be placed on the replay clock.
                logger.warning("UW cache %s has no collected_at; not replayed", path)
                return []
            return [UwSnapshot(collected_at, float(payload.get("aggregate_bias", 0.0)))]
        return None

    def _read_jsonl(self, path: Path) -> list[UwSnapshot]:
        snaps: list[UwSnapshot] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            bundle = row.get("bundle") or row
            bias = (
                float(bundle["aggregate_bias"])
                if "aggregate_bias" in bundle
                else aggregate_flow_bias(bundle)
            )
            collected_at = _parse_collected_at(row.get("collected_at"))
            if collected_at is not None:
                snaps.append(UwSnapshot(collected_at, bias))
        return sorted(snaps, key=lambda s: s.collected_at)

    def flow_signal(
        self,
        day: date,
        as_of: datetime | None = None,
    ) -> tuple[FlowBias, float, bool, str]:
        """
        Returns (bias, strength, block_entries, reason) using the latest snapshot collected
        at or before `as_of` (the whole day when `as_of` is omitted).

        When the cache is missing: UW_BACKTEST_MODE=neutral trades on neutral flow,
        block keeps the session but allows no entries, skip excludes the session.
        """
        mode = self._settings.uw_backtest_mode
        snaps = self.load_snapshots(day)
        if snaps is None:
            if mode == UWBacktestMode.BLOCK:
                return FlowBias.NEUTRAL, 0.0, True, "uw_cache_missing_block"
            if mode == UWBacktestMode.SKIP:
                return FlowBias.NEUTRAL, 0.0, True, "uw_cache_missing_skip"
            return FlowBias.NEUTRAL, 0.0, False, "uw_neutral"
        if as_of is not None:
            snaps = [s for s in snaps if s.collected_at <= as_of]
        if not snaps:
            blocked = mode == UWBacktestMode.BLOCK
            return FlowBias.NEUTRAL, 0.0, blocked, "uw_not_yet_collected_block" if blocked else "uw_not_yet_collected"
        score = snaps[-1].bias
        if score > 0.15:
            return FlowBias.BULLISH, min(1.0, abs(score)), False, "uw_bullish"
        if score < -0.15:
            return FlowBias.BEARISH, min(1.0, abs(score)), False, "uw_bearish"
        return FlowBias.NEUTRAL, 0.15, False, "uw_neutral"
