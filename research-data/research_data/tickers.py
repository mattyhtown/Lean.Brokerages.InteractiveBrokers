from __future__ import annotations

from dataclasses import dataclass
import re

from research_data.http import LoaderError

_SYMBOL = r"[A-Z]{1,5}(?:\.[A-Z])?"
_SYMBOL_RE = re.compile(_SYMBOL)
_CASHTAG_RE = re.compile(rf"(?<![\w$])\$({_SYMBOL})(?![\w])")
_EXCHANGE_RE = re.compile(rf"\b(?:NASDAQ|NYSE|NYSEARCA|AMEX)\s*:\s*({_SYMBOL})(?![\w])")


@dataclass(frozen=True)
class Watchlist:
    """Symbols to look for by bare symbol or company name, on top of $CASHTAG and EXCHANGE: SYMBOL mentions."""

    symbols: tuple[str, ...]
    term_to_symbol: dict[str, str]
    pattern: re.Pattern[str] | None

    @classmethod
    def parse(cls, text: str) -> Watchlist:
        term_to_symbol: dict[str, str] = {}
        symbols: list[str] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            symbol, _, names = line.partition(":")
            symbol = symbol.strip()
            if not _SYMBOL_RE.fullmatch(symbol):
                raise LoaderError(f"Watchlist line {line_number}: {symbol!r} is not a ticker symbol")
            symbols.append(symbol)
            for term in [symbol, *(name.strip() for name in names.split(","))]:
                if not term:
                    continue
                owner = term_to_symbol.setdefault(term, symbol)
                if owner != symbol:
                    raise LoaderError(f"Watchlist line {line_number}: {term!r} is already listed for {owner}")
        pattern = None
        if term_to_symbol:
            longest_first = sorted(term_to_symbol, key=len, reverse=True)
            alternatives = "|".join(re.escape(term) for term in longest_first)
            pattern = re.compile(rf"(?<![\w$])(?:{alternatives})(?![\w])")
        return cls(tuple(symbols), term_to_symbol, pattern)


EMPTY_WATCHLIST = Watchlist((), {}, None)


def extract_tickers(text: str, watchlist: Watchlist = EMPTY_WATCHLIST) -> list[str]:
    found = set(_CASHTAG_RE.findall(text)) | set(_EXCHANGE_RE.findall(text))
    if watchlist.pattern is not None:
        found.update(watchlist.term_to_symbol[match] for match in watchlist.pattern.findall(text))
    return sorted(found)
