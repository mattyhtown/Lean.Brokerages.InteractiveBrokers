from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Callable

from research_data.http import HttpCallable, LoaderError, send
from research_data.newsletters import clean_text

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar"
NO_NEWS = "NONE"
_TIMEOUT_SECONDS = 120
_MAX_TOKENS = 700

_SYSTEM_PROMPT = (
    "You are a financial news wire. Report only what sources published in the last 24 hours say. "
    "Give no advice, no opinion of your own and no introduction."
)
_CITATION_RE = re.compile(r"\s*\[\d+\]")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")
_EMPHASIS_RE = re.compile(r"\*\*|__|`")


@dataclass(frozen=True)
class NewsDigest:
    symbol: str
    model: str
    # Taken after the answer arrived, so nothing in it can be newer than this.
    fetched_at: datetime
    # One sentence per news item; empty when there was no news.
    items: list[str]


def _prompt(symbol: str, names: list[str]) -> str:
    company = f"{names[0]} (ticker {symbol})" if names else f"the company with ticker {symbol}"
    return (
        f"List the material news published in the last 24 hours about {company}: earnings, guidance, "
        "analyst rating or price target changes, deals, products, legal and regulatory developments. "
        "Write one plain-text sentence per item, one item per line, at most 8 items, and name the company "
        f"in each. If there is no such news, reply with exactly: {NO_NEWS}"
    )


def digest_items(answer: str) -> list[str]:
    """The news sentences of a model answer, without citation markers, list markers or emphasis."""
    items: list[str] = []
    for line in clean_text(answer).splitlines():
        line = _EMPHASIS_RE.sub("", _CITATION_RE.sub("", _LIST_MARKER_RE.sub("", line))).strip()
        if line:
            items.append(line)
    if len(items) == 1 and items[0].rstrip(".").upper() == NO_NEWS:
        return []
    return items


def fetch_digest(
    api_key: str,
    symbol: str,
    names: list[str],
    http: HttpCallable = send,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> NewsDigest:
    """Ask Perplexity for the last 24 hours of news about one company."""
    request = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _prompt(symbol, names)},
        ],
        "search_recency_filter": "day",
        "temperature": 0,
        "max_tokens": _MAX_TOKENS,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = http("POST", PERPLEXITY_URL, headers, json.dumps(request).encode("utf-8"), _TIMEOUT_SECONDS)
    try:
        payload = json.loads(response.body)
        answer = payload["choices"][0]["message"]["content"]
        if not isinstance(answer, str):
            raise TypeError(answer)
        model = str(payload.get("model") or MODEL)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise LoaderError(f"Unexpected Perplexity response for {symbol}") from None
    return NewsDigest(symbol, model, clock(), digest_items(answer))
