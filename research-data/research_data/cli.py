from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import sys
from typing import Mapping
from zoneinfo import ZoneInfo

from multi_llm.client import ChatRequest, LLMError, complete

from research_data.adjustments import FactorSnapshot, read_factor_files
from research_data.http import LoaderError
from research_data.local_finbert import LocalFinbert
from research_data.news import NewsDigest, fetch_digest
from research_data.newsletters import fetch_newsletters
from research_data.options import OptionsDay, download_day
from research_data.pipeline import (
    Sentiment,
    load_adjustments,
    load_news,
    load_newsletters,
    load_options,
    load_prices,
    score_passages,
)
from research_data.prices import PriceDownload, download_daily_bars
from research_data.supabase import SupabaseClient
from research_data.tickers import Watchlist

DEFAULT_WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.txt"
_DEFAULT_LOOKBACK_DAYS = 7
_MARKET_TIME_ZONE = ZoneInfo("America/New_York")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise LoaderError(f"Missing {name}")
    return value


def _read_watchlist(path: Path) -> Watchlist:
    try:
        return Watchlist.parse(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LoaderError(f"Cannot read watchlist {path}: {exc.strerror}") from None


def _hosted_finbert(text: str) -> Sentiment:
    try:
        response = complete(ChatRequest(provider="finbert", prompt=text))
    except LLMError as exc:
        raise LoaderError(str(exc)) from None
    scores = {str(item["label"]).lower(): float(item["score"]) for item in response.raw["labels"]}
    return Sentiment(response.model, scores)


def _run_newsletters(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    messages = fetch_newsletters(
        _required(env, "GMAIL_ADDRESS"),
        _required(env, "GMAIL_APP_PASSWORD"),
        args.label,
        args.since,
    )
    newsletters, passages = load_newsletters(supabase, messages, _read_watchlist(args.watchlist))
    print(f"newsletters: read {newsletters} messages since {args.since}, {passages} passages mention a ticker")


def _run_score(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    score = LocalFinbert() if args.finbert == "local" else _hosted_finbert
    scored = score_passages(supabase, score, args.limit)
    print(f"score: scored {scored} passages with {args.finbert} FinBERT")


def _run_prices(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    api_key = _required(env, "DATABENTO_API_KEY")

    def download(symbols: list[str], start: date) -> PriceDownload:
        return download_daily_bars(api_key, symbols, start)

    symbols, bars, warnings = load_prices(supabase, download, _read_watchlist(args.watchlist), args.start)
    print(f"prices: stored {bars} daily bars for {symbols} symbols since {args.start}")
    for warning in warnings:
        print(f"prices: warning: {warning}", file=sys.stderr)


def _run_news(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    api_key = _required(env, "PERPLEXITY_API_KEY")

    def fetch(symbol: str, names: list[str]) -> NewsDigest:
        return fetch_digest(api_key, symbol, names)

    day = datetime.now(_MARKET_TIME_ZONE).date()
    fetched, skipped, items = load_news(supabase, fetch, _read_watchlist(args.watchlist), day)
    print(f"news: fetched {fetched} digests for {day} with {items} news items, {skipped} symbols already had one")


def _run_options(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    token = _required(env, "ORATS_API_TOKEN")

    def download(symbols: list[str], trade_date: date) -> list[OptionsDay]:
        return download_day(token, symbols, trade_date)

    today = datetime.now(_MARKET_TIME_ZONE).date()
    symbols, days, warnings = load_options(supabase, download, _read_watchlist(args.watchlist), args.start, today)
    print(f"options: stored {days} daily rows for {symbols} symbols since {args.start}")
    for warning in warnings:
        print(f"options: warning: {warning}", file=sys.stderr)


def _run_adjustments(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    lean_data = Path(_required(env, "QUANTCONNECT_LEAN_DATA_DIR")).expanduser()

    def read(symbols: list[str]) -> FactorSnapshot:
        return read_factor_files(lean_data, symbols)

    symbols, stored, as_of, warnings = load_adjustments(supabase, read, _read_watchlist(args.watchlist))
    print(
        f"adjustments: stored split and dividend factors for {stored} of {symbols} symbols "
        f"from QuantConnect factor files dated {as_of}"
    )
    for warning in warnings:
        print(f"adjustments: warning: {warning}", file=sys.stderr)


# In the order `all` runs them: text sources before scoring, and the sources that
# follow mentioned tickers after the text that mentions them.
_STEPS = {
    "newsletters": _run_newsletters,
    "news": _run_news,
    "score": _run_score,
    "prices": _run_prices,
    "options": _run_options,
    "adjustments": _run_adjustments,
}
# `all` leaves these out when their setting is missing, so the original three steps keep running without them.
_OPTIONAL_STEP_SETTINGS = {
    "news": "PERPLEXITY_API_KEY",
    "options": "ORATS_API_TOKEN",
    "adjustments": "QUANTCONNECT_LEAN_DATA_DIR",
}


def build_parser() -> argparse.ArgumentParser:
    lookback = date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    parser = argparse.ArgumentParser(
        prog="research-data",
        description="Load newsletters, news digests, FinBERT sentiment, daily prices, options metrics and "
        "split and dividend factors into Supabase.",
    )
    parser.add_argument(
        "step",
        choices=[*_STEPS, "all"],
        help="newsletters: Gmail label to passages; news: today's Perplexity news digest per watchlist symbol; "
        "score: FinBERT on unscored passages; prices: Databento daily bars; options: ORATS daily options "
        "metrics; adjustments: split and dividend factors from QuantConnect factor files on this machine; "
        "all: every step in that order, leaving out news, options and adjustments when their setting is missing",
    )
    parser.add_argument("--label", default="Substack", help="Gmail label to read (default: Substack)")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=lookback,
        help=f"Read newsletters received on or after this date (default: {_DEFAULT_LOOKBACK_DAYS} days ago)",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=lookback,
        help=f"Load prices and options metrics from this date (default: {_DEFAULT_LOOKBACK_DAYS} days ago)",
    )
    parser.add_argument("--limit", type=int, default=1000, help="Most passages to score in one run (default: 1000)")
    parser.add_argument(
        "--finbert",
        choices=["hosted", "local"],
        default="hosted",
        help="Where FinBERT runs: hosted on Hugging Face Inference, which needs HF_TOKEN, or local on this "
        "machine, which needs `poetry install --extras local` and no token (default: hosted)",
    )
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=DEFAULT_WATCHLIST,
        help="Symbols and company names to look for (default: watchlist.txt next to the package)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ
    try:
        supabase = SupabaseClient.from_env(env)
        for name in _STEPS if args.step == "all" else [args.step]:
            setting = _OPTIONAL_STEP_SETTINGS.get(name)
            if args.step == "all" and setting and not env.get(setting, "").strip():
                print(f"{name}: skipped because {setting} is not set", file=sys.stderr)
                continue
            _STEPS[name](args, env, supabase)
    except LoaderError as exc:
        print(f"research-data: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
