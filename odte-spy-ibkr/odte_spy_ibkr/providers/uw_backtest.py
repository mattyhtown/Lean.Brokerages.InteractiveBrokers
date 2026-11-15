from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from odte_spy_ibkr.config import Settings, UWBacktestMode
from odte_spy_ibkr.providers.base import FlowBias


class UwBacktestReplay:
    """Replay Unusual Whales flow from on-disk cache (no live API in backtest)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dir = Path(settings.uw_backtest_cache_dir)

    def load_day(self, day: date) -> dict | None:
        path = self._dir / f"{day.isoformat()}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        jsonl = Path(self._settings.uw_session_jsonl_dir) / f"{day.isoformat()}.jsonl"
        if jsonl.is_file():
            return self._aggregate_jsonl(jsonl, day)
        return None

    def _aggregate_jsonl(self, path: Path, day: date) -> dict | None:
        biases: list[float] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            bundle = row.get("bundle") or row
            if "aggregate_bias" in bundle:
                biases.append(float(bundle["aggregate_bias"]))
                continue
            from odte_spy_ibkr.unusual_whales.signals import aggregate_flow_bias

            biases.append(aggregate_flow_bias(bundle))
        if not biases:
            return None
        return {"date": day.isoformat(), "aggregate_bias": sum(biases) / len(biases), "source": "jsonl"}

    def flow_signal(
        self,
        day: date,
    ) -> tuple[FlowBias, float, bool, str]:
        """
        Returns (bias, strength, block_entries, reason).
        block_entries when UW_BACKTEST_MODE=block and cache missing.
        """
        payload = self.load_day(day)
        mode = self._settings.uw_backtest_mode
        if payload is None:
            if mode == UWBacktestMode.BLOCK:
                return FlowBias.NEUTRAL, 0.0, True, "uw_cache_missing_block"
            if mode == UWBacktestMode.SKIP:
                return FlowBias.NEUTRAL, 0.0, True, "uw_cache_missing_skip"
            return FlowBias.NEUTRAL, 0.0, False, "uw_neutral"
        score = float(payload.get("aggregate_bias", 0.0))
        if score > 0.15:
            return FlowBias.BULLISH, min(1.0, abs(score)), False, "uw_bullish"
        if score < -0.15:
            return FlowBias.BEARISH, min(1.0, abs(score)), False, "uw_bearish"
        return FlowBias.NEUTRAL, 0.15, False, "uw_neutral"