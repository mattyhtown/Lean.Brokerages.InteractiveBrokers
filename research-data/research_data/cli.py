from __future__ import annotations

import argparse
from datetime import date, timedelta
import os
from pathlib import Path
import sys
from typing import Mapping

from multi_llm.client import ChatRequest, LLMError, complete

from research_data.http import LoaderError
from research_data.local_finbert import LocalFinbert
from research_data.newsletters import fetch_newsletters
from research_data.pipeline import Sentiment, load_newsletters, load_prices, score_passages
from research_data.prices import PriceDownload, download_daily_bars
from research_data.supabase import SupabaseClient
from research_data.tickers import Watchlist

DEFAULT_WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.txt"
_DEFAULT_LOOKBACK_DAYS = 7


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


_STEPS = {"newsletters": _run_newsletters, "score": _run_score, "prices": _run_prices}


def build_parser() -> argparse.ArgumentParser:
    lookback = date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    parser = argparse.ArgumentParser(
        prog="research-data",
        description="Load newsletters, FinBERT sentiment and daily prices into Supabase.",
    )
    parser.add_argument(
        "step",
        choices=[*_STEPS, "all"],
        help="newsletters: Gmail label to passages; score: FinBERT on unscored passages; "
        "prices: Databento daily bars; all: the three in that order",
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
        help=f"Load prices from this date (default: {_DEFAULT_LOOKBACK_DAYS} days ago)",
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
            _STEPS[name](args, env, supabase)
    except LoaderError as exc:
        print(f"research-data: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
