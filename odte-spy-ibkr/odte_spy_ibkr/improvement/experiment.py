from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExperimentCandidate:
    name: str
    params: dict[str, Any]


def default_search_space() -> dict[str, list[Any]]:
    return {
        "min_minutes_after_open": [20, 30, 45],
        "min_credit": [0.10, 0.15, 0.20],
        "short_delta_min": [0.08, 0.10, 0.12],
        "short_delta_max": [0.18, 0.20, 0.22],
        "uw_min_strength": [0.25, 0.35, 0.45],
    }


def grid_candidates(limit: int = 12) -> list[ExperimentCandidate]:
    space = default_search_space()
    keys = list(space)
    out: list[ExperimentCandidate] = []
    for i in range(limit):
        params = {k: random.choice(space[k]) for k in keys}
        out.append(ExperimentCandidate(name=f"candidate_{i+1}", params=params))
    return out