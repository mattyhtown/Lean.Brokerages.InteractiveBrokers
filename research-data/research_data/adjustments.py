from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from pathlib import Path, PurePosixPath
import re
import zipfile

from research_data.http import LoaderError

# Where `lean data download --dataset "US Equity Security Master"` puts factor files in a LEAN data folder.
FACTOR_FILES_FOLDER = Path("equity", "usa", "factor_files")
DOWNLOAD_COMMAND = 'lean data download --dataset "US Equity Security Master"'
_ARCHIVE_RE = re.compile(r"factor_files_(\d{8})\.zip")
_DATE_RE = re.compile(r"\d{8}")
# A real factor file is a few kilobytes; this only stops a corrupt archive from filling memory.
_MAX_FACTOR_FILE_BYTES = 5_000_000


@dataclass(frozen=True)
class FactorRow:
    """One line of a LEAN factor file.

    A raw price dated on or before `through_date`, and after the row before it, times both
    factors is that price on the scale of the latest prices. `through_date` is the last trading
    day before a split or dividend takes effect.
    """

    through_date: date
    price_factor: float
    split_factor: float


@dataclass(frozen=True)
class FactorSnapshot:
    # The date in the archive's name: splits and dividends after it are not in the rows.
    as_of: date
    rows_by_symbol: dict[str, list[FactorRow]]
    # Requested symbols the archive has no factor file for.
    missing: list[str]


def parse_factor_file(text: str, name: str) -> list[FactorRow]:
    """Read `date,price factor,split factor[,reference price]` lines the way LEAN does.

    LEAN leaves out lines with an infinite or exponent-notation factor and rows whose factors
    multiply to zero, and uses the first row when a date repeats.
    """
    rows: dict[date, FactorRow] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "inf" in line or "e+" in line:
            continue
        fields = line.split(",")
        try:
            if not _DATE_RE.fullmatch(fields[0]):
                raise ValueError(fields[0])
            through_date = datetime.strptime(fields[0], "%Y%m%d").date()
            price_factor, split_factor = float(fields[1]), float(fields[2])
            if not (math.isfinite(price_factor) and math.isfinite(split_factor)):
                raise ValueError(line)
        except (ValueError, IndexError):
            raise LoaderError(f"Unexpected line in QuantConnect factor file {name}: {line[:80]!r}") from None
        if price_factor * split_factor > 0:
            rows.setdefault(through_date, FactorRow(through_date, price_factor, split_factor))
    return [rows[through_date] for through_date in sorted(rows)]


def _latest_archive(folder: Path) -> tuple[date, Path]:
    try:
        names = [path.name for path in folder.iterdir()]
    except OSError as exc:
        raise LoaderError(f"Cannot read {folder}: {exc.strerror}. Run `{DOWNLOAD_COMMAND}` first.") from None
    dated: list[tuple[date, str]] = []
    for name in names:
        match = _ARCHIVE_RE.fullmatch(name)
        if match:
            try:
                dated.append((datetime.strptime(match.group(1), "%Y%m%d").date(), name))
            except ValueError:
                continue
    if not dated:
        raise LoaderError(f"No factor_files_YYYYMMDD.zip in {folder}. Run `{DOWNLOAD_COMMAND}` first.")
    as_of, name = max(dated)
    return as_of, folder / name


def read_factor_files(lean_data: Path, symbols: list[str]) -> FactorSnapshot:
    """Read the factor files of `symbols` from the newest factor file archive in a LEAN data folder.

    Nothing is downloaded or bought: the archive is whatever the LEAN CLI last put there.
    """
    as_of, archive_path = _latest_archive(lean_data / FACTOR_FILES_FOLDER)
    rows_by_symbol: dict[str, list[FactorRow]] = {}
    missing: list[str] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            # Named by ticker in lower case, like aapl.csv and brk.b.csv.
            entries = {
                PurePosixPath(entry.filename).name.lower(): entry for entry in archive.infolist() if not entry.is_dir()
            }
            for symbol in symbols:
                entry = entries.get(f"{symbol.lower()}.csv")
                if entry is None:
                    missing.append(symbol)
                    continue
                if entry.file_size > _MAX_FACTOR_FILE_BYTES:
                    raise LoaderError(f"{entry.filename} in {archive_path} is too large to be a factor file")
                text = archive.read(entry).decode("utf-8")
                rows_by_symbol[symbol] = parse_factor_file(text, f"{archive_path.name}/{entry.filename}")
    except (zipfile.BadZipFile, UnicodeDecodeError):
        raise LoaderError(f"{archive_path} is not a readable factor file archive") from None
    except OSError as exc:
        raise LoaderError(f"Cannot read {archive_path}: {exc.strerror}") from None
    return FactorSnapshot(as_of, rows_by_symbol, missing)
