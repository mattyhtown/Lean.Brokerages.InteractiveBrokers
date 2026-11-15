from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class SessionLog:
    day: date
    reason: str
    traded: bool = False
    pnl: float = 0.0
    had_open_at_open: bool = False


@dataclass
class VerboseSessionReport:
    sessions: list[SessionLog] = field(default_factory=list)
    reason_counts: dict[str, int] = field(default_factory=dict)

    def add(self, row: SessionLog) -> None:
        self.sessions.append(row)
        self.reason_counts[row.reason] = self.reason_counts.get(row.reason, 0) + 1