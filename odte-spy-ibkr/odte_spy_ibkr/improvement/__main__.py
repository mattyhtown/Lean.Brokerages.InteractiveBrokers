from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from odte_spy_ibkr.config import load_settings
from odte_spy_ibkr.improvement.evaluator import evaluate_candidate
from odte_spy_ibkr.improvement.experiment import grid_candidates
from odte_spy_ibkr.improvement.promoter import should_promote
from odte_spy_ibkr.improvement.registry import load_active_params, save_active_params, write_history
from odte_spy_ibkr.logging_utils import setup_logging

logger = logging.getLogger(__name__)


def run_improvement() -> int:
    settings = load_settings()
    active = load_active_params(settings.params_active_path)
    challengers = grid_candidates(limit=5)
    results = [evaluate_candidate(c) for c in challengers]
    best = max(results, key=lambda r: r.score)

    history_payload = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "active_sharpe": active.get("metrics", {}).get("sharpe_rolling"),
        "best_candidate": best.candidate.name,
        "best_sharpe": best.max_rolling_sharpe,
        "best_drawdown": best.max_drawdown,
        "best_score": best.score if best.max_rolling_sharpe is not None else None,
        "params": best.candidate.params,
        "best_sample": getattr(best, "sample", {}),
    }
    write_history(settings.params_history_dir, history_payload)

    decision = should_promote(settings, active, best)
    if decision.promoted:
        for key, value in best.candidate.params.items():
            if key.startswith("macro_"):
                active.setdefault("macro", {})[key.removeprefix("macro_")] = value
            else:
                active.setdefault("strategy", {})[key] = value
        active["metrics"] = {
            "sharpe_rolling": best.max_rolling_sharpe,
            "max_drawdown": best.max_drawdown,
        }
        active["promoted_at"] = history_payload["timestamp"]
        save_active_params(settings.params_active_path, active)
        logger.info("Promoted candidate %s: %s", best.candidate.name, decision.reason)
    else:
        logger.info("No promotion: %s", decision.reason)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="ODTE improvement loop")
    parser.add_argument("command", nargs="?", default="run")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    if args.command == "run":
        raise SystemExit(run_improvement())
    raise SystemExit(2)


if __name__ == "__main__":
    main()