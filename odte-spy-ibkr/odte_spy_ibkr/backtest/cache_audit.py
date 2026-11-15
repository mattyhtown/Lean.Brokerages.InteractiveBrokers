"""Integrity checks for cached market data, and a tool to list or purge files that fail them.

A cache file named for a session must hold that session's own data. Earlier versions of the
chain loader wrote today's Alpaca chain snapshot, a generated chain, or an empty chain (after
an HTTP error) under whatever historical date was being backtested, and cached a daily bar
where intraday bars are expected. The loader rejects every such file; this module names them.

    python -m odte_spy_ibkr.backtest.cache_audit            # list
    python -m odte_spy_ibkr.backtest.cache_audit --purge    # delete
"""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from odte_spy_ibkr.backtest.calendar import ET, session_close, session_open
from odte_spy_ibkr.backtest.store import read_rows
from odte_spy_ibkr.config import load_settings

OCC_SYMBOL = re.compile(r"^[A-Z]{1,6}(\d{6})[CP]\d{8}$")
NON_TICKER_DIRS = {"bars", "macro_news", "macro_calendar", "uw"}


def _parse_ts(raw: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def symbol_expiry(symbol: Any) -> date | None:
    """Expiry encoded in an OCC option symbol; None for anything else."""
    match = OCC_SYMBOL.match(str(symbol or "").replace(" ", ""))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%m%d").date()
    except ValueError:
        return None


def is_bar_rows(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and "t" in rows[0] and "c" in rows[0]


def in_session(raw_ts: Any, day: date) -> bool:
    """A bar start time inside `day`'s regular session."""
    ts = _parse_ts(raw_ts)
    return ts is not None and session_open(day) <= ts < session_close(day)


def session_bar_rows(rows: list[dict[str, Any]] | None, day: date) -> list[dict[str, Any]]:
    """1-minute bars of `day`'s own session and, for options, of contracts expiring on `day`.

    Anything else in the file is dropped: a daily bar, another day's bars, another expiry.
    """
    if not rows or not is_bar_rows(rows):
        return []
    return [
        row
        for row in rows
        if in_session(row.get("t"), day) and symbol_expiry(row.get("symbol")) in (None, day)
    ]


def chain_snapshot_problem(rows: list[dict[str, Any]] | None, day: date) -> str | None:
    """Why a cached chain snapshot cannot be replayed as `day`'s chain; None when it can."""
    if not rows:
        return "empty_chain"
    captured = {_parse_ts(row.get("snapshot_at")) for row in rows}
    if None in captured:
        # Without a capture time the quotes cannot be placed on that day's clock at all.
        return "no_capture_time"
    for at in captured:
        if at.astimezone(ET).date() != day:
            return f"captured_on_{at.astimezone(ET).date().isoformat()}"
        if not session_open(day) <= at <= session_close(day):
            return "captured_outside_session"
    if any(symbol_expiry(row.get("symbol")) not in (None, day) for row in rows):
        return "contracts_of_other_expiry"
    return None


def chain_file_problem(rows: list[dict[str, Any]] | None, day: date) -> str | None:
    if rows and is_bar_rows(rows):
        kept = session_bar_rows(rows, day)
        if not kept:
            return "no_bars_in_session"
        return None if len(kept) == len(rows) else "bars_of_other_session_or_expiry"
    return chain_snapshot_problem(rows, day)


def spot_file_problem(rows: list[dict[str, Any]] | None, day: date) -> str | None:
    if not rows:
        return "empty"
    if not session_bar_rows(rows, day):
        # Typically a single daily bar: its close is not known during the session.
        return "no_intraday_bars"
    return None


def _session_files(directory: Path) -> dict[date, list[Path]]:
    by_day: dict[date, list[Path]] = {}
    for path in sorted(directory.glob("*")):
        if path.suffix not in {".parquet", ".json"}:
            continue
        try:
            by_day.setdefault(date.fromisoformat(path.stem), []).append(path)
        except ValueError:
            continue
    return by_day


def audit_cache(cache_dir: str | Path) -> list[tuple[Path, str]]:
    """(file, problem) for every chain and spot cache file that the loader will not use."""
    root = Path(cache_dir)
    findings: list[tuple[Path, str]] = []
    if not root.is_dir():
        return findings
    targets = [(d, chain_file_problem) for d in sorted(root.iterdir()) if d.is_dir() and d.name not in NON_TICKER_DIRS]
    bars = root / "bars"
    if bars.is_dir():
        targets += [(d, spot_file_problem) for d in sorted(bars.iterdir()) if d.is_dir()]
    for directory, check in targets:
        for day, paths in _session_files(directory).items():
            problem = check(read_rows(paths[0].with_suffix(".parquet")), day)
            if problem:
                findings += [(path, problem) for path in paths]
    return findings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List or purge cached chain/spot files that are not their session's own data")
    parser.add_argument("--cache-dir", default=None, help="Cache root (default: BACKTEST_CACHE_DIR)")
    parser.add_argument("--purge", action="store_true", help="Delete the listed files")
    args = parser.parse_args(argv)
    findings = audit_cache(args.cache_dir or load_settings().backtest_cache_dir)
    for path, problem in findings:
        print(f"{'purged' if args.purge else 'suspect'}\t{problem}\t{path}")
        if args.purge:
            path.unlink()
    print(f"{len(findings)} file(s) {'purged' if args.purge else 'would be rejected by the backtest loader'}")


if __name__ == "__main__":
    main()
