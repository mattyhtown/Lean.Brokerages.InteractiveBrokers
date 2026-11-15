from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class DailyLedger:
    equity_base: float = 100_000.0
    rows: dict[date, float] = field(default_factory=dict)

    def record(self, day: date, pnl: float) -> None:
        self.rows[day] = self.rows.get(day, 0.0) + pnl

    def daily_pnl_series(self, days: list[date]) -> list[float]:
        return [self.rows.get(d, 0.0) for d in days]

    def daily_return_series(self, days: list[date]) -> list[float]:
        equity = self.equity_base
        out: list[float] = []
        for d in days:
            pnl = self.rows.get(d, 0.0)
            out.append(pnl / equity if equity else 0.0)
            equity += pnl
        return out