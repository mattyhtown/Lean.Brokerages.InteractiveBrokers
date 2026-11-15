from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Iterable


@dataclass(frozen=True)
class TradeRecord:
    """One round trip. Credit and debit are per share; commission and pnl are dollars."""

    day: date
    side: str
    short_strike: float
    long_strike: float
    contracts: int
    opened_at: datetime
    closed_at: datetime
    entry_credit: float
    exit_debit: float
    commission: float
    pnl: float
    exit_reason: str

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ("day", "opened_at", "closed_at"):
            row[key] = row[key].isoformat()
        return row


@dataclass
class DailyLedger:
    """Realized PnL per session. Returns are PnL over equity at the start of that day
    (`equity_base` plus all earlier PnL)."""

    equity_base: float = 100_000.0
    rows: dict[date, float] = field(default_factory=dict)
    trades: list[TradeRecord] = field(default_factory=list)

    def record(self, day: date, pnl: float, trades: Iterable[TradeRecord] = ()) -> None:
        self.rows[day] = self.rows.get(day, 0.0) + pnl
        self.trades.extend(trades)

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

    def total_pnl(self, days: list[date] | None = None) -> float:
        if days is None:
            return float(sum(self.rows.values()))
        return float(sum(self.rows.get(d, 0.0) for d in days))
