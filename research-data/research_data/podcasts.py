from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from email.utils import parsedate_to_datetime
import hashlib
from pathlib import Path
import re
import tempfile
from typing import Callable
import urllib.parse
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from research_data.http import LoaderError, PublicFetch, fetch_public
from research_data.newsletters import Passage, clean_text, passage_chunks
from research_data.tickers import Watchlist, extract_tickers

MAX_FEED_BYTES = 64_000_000
MAX_TRANSCRIPT_BYTES = 8_000_000
MAX_AUDIO_BYTES = 1_000_000_000

# What `transcript_source` holds for a transcript the feed linked to.
PUBLISHER = "publisher"

_PODCAST_NAMESPACE = "{https://podcastindex.org/namespace/1.0}"
# In the order they are tried. Plain text comes first because it reads as written; captions
# cut sentences into screen lines and are joined back together.
_TRANSCRIPT_TYPES = ("text/plain", "text/vtt", "application/srt", "application/x-subrip")
_CUE_TIMING = "-->"
_TAG_RE = re.compile(r"<[^>]*>")
_TIMESTAMP = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?"
_TIMESTAMP_LINE_RE = re.compile(rf"\[?{_TIMESTAMP}\]?")
_LEADING_TIMESTAMP_RE = re.compile(rf"\[{_TIMESTAMP}\]\s*")
# Where a show tells its listeners to find it: an app, not the company as a business.
_LISTENING_APP_RE = re.compile(r"\b(?:(?:Apple|Google) Podcasts?|Amazon Music)\b")


class EpisodeUnavailable(LoaderError):
    """One episode could not be turned into text; the others are unaffected."""


@dataclass(frozen=True)
class FeedLine:
    line: int
    url: str

    @property
    def label(self) -> str:
        """Names the feed without its address, which for a private feed is a password."""
        return f"line {self.line} ({urllib.parse.urlparse(self.url).hostname})"


@dataclass(frozen=True)
class Episode:
    id: str
    show: str
    title: str
    published_at: datetime
    # The publisher's transcripts in formats this module reads, best first.
    transcript_urls: tuple[str, ...]
    audio_url: str | None


@dataclass(frozen=True)
class Feed:
    show: str
    # Published on or after the date asked for, oldest first.
    episodes: list[Episode]
    # Episodes left out because they have no readable publication time.
    undated: int


@dataclass(frozen=True)
class Transcript:
    text: str
    # PUBLISHER, or the name of the speech model that made the text.
    source: str


AudioTranscriber = Callable[[Path], Transcript]


def read_feed_list(text: str) -> list[FeedLine]:
    """Feed addresses, one per line; `#` starts a comment."""
    feeds: list[FeedLine] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        url = raw_line.split("#", 1)[0].strip()
        if not url:
            continue
        if not url.startswith("https://") or any(character.isspace() for character in url):
            raise LoaderError(f"Podcast list line {number} is not an https:// feed address")
        if all(feed.url != url for feed in feeds):
            feeds.append(FeedLine(number, url))
    return feeds


def _text(element: Element, tag: str) -> str:
    return " ".join((element.findtext(tag) or "").replace("\x00", "").split())


def _published_at(item: Element) -> datetime | None:
    try:
        published = parsedate_to_datetime(_text(item, "pubDate"))
    except ValueError:
        return None
    return published if published.tzinfo else published.replace(tzinfo=timezone.utc)


def parse_feed(body: bytes, since: date) -> Feed:
    """The episodes of an RSS podcast feed published on or after `since` (UTC)."""
    try:
        root = fromstring(body)
    except (ParseError, DefusedXmlException):
        raise LoaderError("it is not an RSS podcast feed") from None
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise LoaderError("it is not an RSS podcast feed")
    show = _text(channel, "title")
    earliest = datetime.combine(since, time.min, tzinfo=timezone.utc)
    episodes: dict[str, Episode] = {}
    undated = 0
    for item in channel.findall("item"):
        published_at = _published_at(item)
        if published_at is None:
            undated += 1
            continue
        if published_at < earliest:
            continue
        enclosure = item.find("enclosure")
        audio_url = (enclosure.get("url") or "").strip() if enclosure is not None else ""
        links = {
            (link.get("type") or "").strip().lower(): (link.get("url") or "").strip()
            for link in reversed(item.findall(f"{_PODCAST_NAMESPACE}transcript"))
        }
        # A feed that moves to another host keeps its episode guids, so the guid alone names the episode.
        identity = _text(item, "guid") or audio_url
        if not identity:
            continue
        episode_id = "podcast:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        episodes.setdefault(
            episode_id,
            Episode(
                id=episode_id,
                show=show,
                title=_text(item, "title"),
                published_at=published_at,
                transcript_urls=tuple(links[kind] for kind in _TRANSCRIPT_TYPES if links.get(kind)),
                audio_url=audio_url or None,
            ),
        )
    return Feed(show, sorted(episodes.values(), key=lambda episode: episode.published_at), undated)


def transcript_text(body: bytes) -> str:
    """A published transcript as one running text, without cue numbers, timings or caption markup."""
    lines = body.decode("utf-8-sig", errors="replace").splitlines()
    spoken: list[str] = []
    if any(_CUE_TIMING in line for line in lines) or body.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT"):
        in_cue = False
        for line in lines:
            if _CUE_TIMING in line:
                in_cue = True
            elif not line.strip():
                in_cue = False
            elif in_cue:
                spoken.append(_TAG_RE.sub("", line))
    else:
        for line in lines:
            if not _TIMESTAMP_LINE_RE.fullmatch(line.strip()):
                spoken.append(_LEADING_TIMESTAMP_RE.sub("", line.strip(), count=1))
    return clean_text(" ".join(spoken))


def episode_passages(text: str, watchlist: Watchlist) -> list[Passage]:
    """The passages of a transcript that mention a ticker, other than by naming a listening app."""
    passages = []
    for index, chunk in enumerate(passage_chunks(text)):
        tickers = extract_tickers(_LISTENING_APP_RE.sub(" ", chunk), watchlist)
        if tickers:
            passages.append(Passage(index, chunk, tickers))
    return passages


def read_feed(feed: FeedLine, since: date, fetch: PublicFetch = fetch_public) -> Feed:
    return parse_feed(fetch(feed.url, MAX_FEED_BYTES, None).body, since)


def episode_transcript(
    episode: Episode,
    transcribe: AudioTranscriber | None,
    fetch: PublicFetch = fetch_public,
) -> Transcript | None:
    """The episode as text: the publisher's transcript when one can be read, otherwise its audio transcribed.

    Returns None when there is no transcript to read and no `transcribe` to make one with.
    """
    failure: LoaderError | None = None
    for url in episode.transcript_urls:
        try:
            text = transcript_text(fetch(url, MAX_TRANSCRIPT_BYTES, None).body)
        except LoaderError as exc:
            failure = exc
            continue
        if text:
            return Transcript(text, PUBLISHER)
    if transcribe is None:
        if failure is not None:
            raise EpisodeUnavailable(f"its transcript could not be read: {failure}")
        return None
    if episode.audio_url is None:
        raise EpisodeUnavailable("it has neither a readable transcript nor audio")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "episode.audio"
        # Closed before it is transcribed: Windows refuses a second open of a file that is still open for writing.
        with path.open("wb") as audio:
            try:
                fetch(episode.audio_url, MAX_AUDIO_BYTES, audio)
            except LoaderError as exc:
                raise EpisodeUnavailable(f"its audio could not be downloaded: {exc}") from None
        transcript = transcribe(path)
    text = clean_text(transcript.text)
    if not text:
        raise EpisodeUnavailable("no speech was found in its audio")
    return Transcript(text, transcript.source)
