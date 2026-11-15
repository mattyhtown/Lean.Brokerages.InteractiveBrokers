from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# LEAN equity trade minute zip: data/equity/usa/minute/spy/20260701_trade.zip
EQUITY_MINUTE = re.compile(r"(?P<day>\d{8})_trade\.zip$", re.IGNORECASE)

# LEAN US equity option minute quote zip (Algoseek layout on disk)
OPTION_QUOTE_ZIP = re.compile(r"(?P<day>\d{8})_quote_(?P<style>american|european)\.zip$", re.IGNORECASE)

# Option contract symbol in filename: spy_160118C00195000_quote_american.zip
OPTION_CONTRACT_ZIP = re.compile(
    r"spy_(?P<exp>\d{6})(?P<right>[CP])(?P<strike>\d+)_.*quote.*\.zip$",
    re.IGNORECASE,
)


def resolve_lean_data_root(configured: str) -> Path:
    if configured.strip():
        return Path(configured).expanduser()
    return Path("data/quantconnect/lean")


def find_equity_minute_zip(root: Path, *, symbol: str, day: date) -> Path | None:
    sym = symbol.lower()
    ymd = day.strftime("%Y%m%d")
    candidates = [
        root / "equity" / "usa" / "minute" / sym / f"{ymd}_trade.zip",
        root / "equity" / "usa" / "minute" / sym / f"{ymd}_trade_unzip" / f"{sym}.csv",
    ]
    for path in candidates:
        if path.is_file():
            return path
    folder = root / "equity" / "usa" / "minute" / sym
    if folder.is_dir():
        for child in sorted(folder.glob(f"{ymd}_trade*.zip")):
            return child
    return None


def parse_lean_equity_minute_lines(text: str, *, day: date) -> list[dict[str, Any]]:
    """
    LEAN trade minute rows: ``YYYYMMDD HH:MM,open,high,low,close,volume``.
    Output matches Alpaca research minute JSON: ``t,o,h,l,c,v``.
    """
    bars: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 6:
            continue
        ts_raw = parts[0].strip()
        try:
            bar_day = datetime.strptime(ts_raw[:8], "%Y%m%d").date()
            hm = ts_raw[9:].strip() if len(ts_raw) > 8 else "00:00"
            hour, minute = (int(x) for x in hm.split(":")[:2])
            dt = datetime.combine(bar_day, time(hour, minute), tzinfo=ET)
        except ValueError:
            continue
        if bar_day != day:
            continue
        try:
            o, h, low, c, vol = (float(parts[i]) for i in range(1, 6))
        except ValueError:
            continue
        bars.append(
            {
                "t": dt.isoformat(),
                "o": o,
                "h": h,
                "l": low,
                "c": c,
                "v": vol,
            }
        )
    bars.sort(key=lambda b: b["t"])
    return bars


def load_equity_minute_bars(root: Path, *, symbol: str, day: date) -> list[dict[str, Any]]:
    path = find_equity_minute_zip(root, symbol=symbol, day=day)
    if path is None:
        return []
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                return []
            text = zf.read(names[0]).decode("utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return parse_lean_equity_minute_lines(text, day=day)


def _ms_to_time(ms: int) -> time:
    sec = ms // 1000
    h, rem = divmod(sec, 3600)
    m, _ = divmod(rem, 60)
    return time(h, m)


def _parse_option_quote_zip(path: Path, *, day: date) -> list[dict[str, Any]]:
    strike: float | None = None
    right: str | None = None
    m = OPTION_CONTRACT_ZIP.search(path.name)
    if m:
        right = m.group("right").upper()
        strike = int(m.group("strike")) / 1000.0
    rows: list[tuple[int, float, float]] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                return []
            text = zf.read(names[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        logger.warning("Bad option zip %s", path)
        return []
    reader = csv.reader(io.StringIO(text))
    for parts in reader:
        if len(parts) < 3:
            continue
        try:
            if ":" in parts[0]:
                ms = _time_label_to_ms(parts[0], day=day)
            else:
                ms = int(parts[0])
            bid = float(parts[1])
            ask = float(parts[2]) if len(parts) > 2 else bid
        except ValueError:
            continue
        rows.append((ms, bid, ask))
    if strike is None or right is None:
        return []
    return [
        {
            "ms": ms,
            "strike": strike,
            "right": right,
            "bid": bid,
            "ask": ask,
        }
        for ms, bid, ask in rows
    ]


def _time_label_to_ms(label: str, *, day: date) -> int:
    """LEAN sometimes stores ``YYYYMMDD HH:MM`` in option quote files."""
    label = label.strip()
    if len(label) >= 13 and label[8] == " ":
        hm = label[9:14]
        hour, minute = (int(x) for x in hm.split(":"))
        base = datetime.combine(day, time(hour, minute), tzinfo=ET)
        midnight = datetime.combine(day, time(0, 0), tzinfo=ET)
        return int((base - midnight).total_seconds() * 1000)
    return int(label)


def list_option_quote_files(root: Path, *, symbol: str, day: date) -> list[Path]:
    sym = symbol.lower()
    ymd = day.strftime("%Y%m%d")
    out: list[Path] = []
    bases = [
        root / "option" / "usa" / "minute" / sym,
        root / "option" / "usa" / "minute",
    ]
    for base in bases:
        if not base.is_dir():
            continue
        out.extend(sorted(base.glob(f"*{ymd}*quote*.zip")))
        out.extend(sorted(base.glob(f"{sym}_{ymd[2:]}*.zip")))
        out.extend(sorted(base.glob(f"{sym}_*{ymd[2:]}*.zip")))
    # De-dup
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def chain_rows_at_times(
    root: Path,
    *,
    symbol: str,
    day: date,
    signal_time: time,
    close_time: time,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Aggregate LEAN per-contract option minute quote zips into unified chain rows
  at signal and close ET windows.
    """
    ms_signal = (signal_time.hour * 3600 + signal_time.minute * 60) * 1000
    ms_close = (close_time.hour * 3600 + close_time.minute * 60) * 1000
    by_key: dict[tuple[float, str], dict[str, Any]] = {}
    for path in list_option_quote_files(root, symbol=symbol, day=day):
        quotes = _parse_option_quote_zip(path, day=day)
        if not quotes:
            continue
        strike = quotes[0]["strike"]
        right = quotes[0]["right"]
        key = (strike, right)
        best_sig = min(quotes, key=lambda q: abs(q["ms"] - ms_signal))
        best_close = min(quotes, key=lambda q: abs(q["ms"] - ms_close))
        entry = by_key.setdefault(
            key,
            {
                "symbol": f"LEAN-{day.strftime('%y%m%d')}{right}{int(round(strike * 1000)):08d}",
                "strike": strike,
                "right": right,
                "bid": None,
                "ask": None,
                "close_bid": None,
                "close_ask": None,
                "volume": 0.0,
                "open_interest": 0.0,
            },
        )
        entry["bid"] = best_sig["bid"]
        entry["ask"] = best_sig["ask"]
        entry["close_bid"] = best_close["bid"]
        entry["close_ask"] = best_close["ask"]
    signal_rows: list[dict[str, Any]] = []
    close_rows: list[dict[str, Any]] = []
    yymmdd = day.strftime("%y%m%d")
    for (_strike, right), row in sorted(by_key.items(), key=lambda x: (x[0][1], x[0][0])):
        if row.get("bid") is None and row.get("ask") is None:
            continue
        bid, ask = row["bid"], row["ask"]
        if bid is None:
            bid = ask
        if ask is None:
            ask = bid
        signal_rows.append(
            {
                "symbol": row["symbol"] or f"SPY{yymmdd}{right}{int(round(_strike * 1000)):08d}",
                "strike": _strike,
                "right": right,
                "bid": bid,
                "ask": ask,
                "volume": 0.0,
                "open_interest": 0.0,
            }
        )
        cb, ca = row.get("close_bid"), row.get("close_ask")
        if cb is None and ca is None:
            continue
        if cb is None:
            cb = ca
        if ca is None:
            ca = cb
        close_rows.append(
            {
                "symbol": signal_rows[-1]["symbol"],
                "strike": _strike,
                "right": right,
                "bid": cb,
                "ask": ca,
                "volume": 0.0,
                "open_interest": 0.0,
            }
        )
    return signal_rows, close_rows