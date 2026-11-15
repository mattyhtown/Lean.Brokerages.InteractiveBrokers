from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from odte_spy_ibkr.config import load_settings

BROKER_SOURCE = "broker"
DATE_COLUMNS = ("date", "tradedate", "reportdate", "settledate")
PNL_COLUMNS = ("pnl", "realizedpnl", "fifopnlrealized", "realized_pnl", "mtmpnl")


@dataclass(frozen=True)
class DailyPnlRow:
    day: date
    pnl: float
    source: str = "paper"


def append_daily_pnl(path: str | Path, row: DailyPnlRow) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": row.day.isoformat(), "pnl": row.pnl, "source": row.source}) + "\n")


def read_journal_rows(path: str | Path) -> list[DailyPnlRow]:
    p = Path(path)
    if not p.is_file():
        return []
    rows: list[DailyPnlRow] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            rows.append(
                DailyPnlRow(
                    day=date.fromisoformat(str(payload["date"])),
                    pnl=float(payload.get("pnl", 0.0)),
                    source=str(payload.get("source", "paper")),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return rows


def read_daily_pnl(path: str | Path) -> dict[date, float]:
    """Realized PnL per date, oldest first.

    Rows for one date are summed. Where a date has broker-statement rows they replace the
    runner's own rows for that date, since the statement is the record of what filled.
    """
    by_day: dict[date, dict[bool, float]] = {}
    for row in read_journal_rows(path):
        bucket = by_day.setdefault(row.day, {})
        is_broker = row.source == BROKER_SOURCE
        bucket[is_broker] = bucket.get(is_broker, 0.0) + row.pnl
    return {day: by_day[day].get(True, by_day[day].get(False, 0.0)) for day in sorted(by_day)}


def _parse_day(raw: str) -> date:
    raw = raw.strip().split(";")[0].split(" ")[0]
    if len(raw) == 8 and raw.isdigit():
        return datetime.strptime(raw, "%Y%m%d").date()
    return date.fromisoformat(raw[:10])


def _pick_column(fieldnames: list[str], wanted: str | None, known: tuple[str, ...], kind: str) -> str:
    if wanted:
        if wanted not in fieldnames:
            raise ValueError(f"{kind} column {wanted!r} not in CSV header {fieldnames}")
        return wanted
    for name in fieldnames:
        if name.strip().lower() in known:
            return name
    raise ValueError(f"no {kind} column found in CSV header {fieldnames}; pass it explicitly")


def ingest_broker_csv(
    csv_path: str | Path,
    journal_path: str | Path,
    *,
    date_col: str | None = None,
    pnl_col: str | None = None,
) -> int:
    """Load realized PnL per day from a broker statement CSV (for example an IBKR Flex
    query) into the journal. Returns the number of dates written.

    Dates in the CSV replace any broker rows already journaled for them, so re-ingesting
    the same or an extended statement does not double count.
    """
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        date_name = _pick_column(fieldnames, date_col, DATE_COLUMNS, "date")
        pnl_name = _pick_column(fieldnames, pnl_col, PNL_COLUMNS, "pnl")
        totals: dict[date, float] = {}
        for record in reader:
            raw_day, raw_pnl = record.get(date_name), record.get(pnl_name)
            if not raw_day or raw_pnl in (None, ""):
                continue
            day = _parse_day(raw_day)
            totals[day] = totals.get(day, 0.0) + float(str(raw_pnl).replace(",", ""))

    kept = [r for r in read_journal_rows(journal_path) if not (r.source == BROKER_SOURCE and r.day in totals)]
    rows = kept + [DailyPnlRow(day=d, pnl=pnl, source=BROKER_SOURCE) for d, pnl in totals.items()]
    rows.sort(key=lambda r: r.day)
    out = Path(journal_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps({"date": r.day.isoformat(), "pnl": r.pnl, "source": r.source}) + "\n" for r in rows),
        encoding="utf-8",
    )
    return len(totals)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ODTE daily PnL journal")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="Load realized daily PnL from a broker statement CSV")
    ingest.add_argument("--csv", required=True)
    ingest.add_argument("--journal", default=None, help="Journal path (default: JOURNAL_PATH)")
    ingest.add_argument("--date-col", default=None)
    ingest.add_argument("--pnl-col", default=None)
    args = parser.parse_args(argv)
    journal = args.journal or load_settings().journal_path
    count = ingest_broker_csv(args.csv, journal, date_col=args.date_col, pnl_col=args.pnl_col)
    print(f"{count} day(s) written to {journal}")


if __name__ == "__main__":
    main()
