from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import email
import email.policy
import email.utils
from html.parser import HTMLParser
import imaplib
import re
import ssl
import time
from typing import Callable, Iterator, Protocol

from research_data.http import LoaderError
from research_data.tickers import Watchlist, extract_tickers

GMAIL_IMAP_HOST = "imap.gmail.com"
_IMAP_TIMEOUT_SECONDS = 60
# FinBERT reads at most 512 tokens; passages this short stay well inside that.
MAX_PASSAGE_CHARS = 800

_MSGID_RE = re.compile(rb"X-GM-MSGID (\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE "[^"]+"')
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_BRACKETED_LINK_RE = re.compile(r"\[\s*https?://[^\]\s]*\s*\]")
_URL_RE = re.compile(r"https?://\S+")
# Substack pads its preview line with invisible characters.
_INVISIBLE_RE = re.compile("[\u00ad\u034f\u200b-\u200f\u2060\ufeff\x00]")
_SPACES_RE = re.compile(r"[ \t\u00a0]+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
_BLOCK_TAGS = frozenset({"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"})
_SKIPPED_TAGS = frozenset({"script", "style", "head"})


@dataclass(frozen=True)
class Newsletter:
    id: str
    sender: str
    publication: str | None
    subject: str
    received_at: datetime
    body_text: str


@dataclass(frozen=True)
class Passage:
    index: int
    text: str
    tickers: list[str]


class ImapConnection(Protocol):
    def login(self, user: str, password: str) -> object: ...
    def select(self, mailbox: str, readonly: bool) -> tuple[str, list[object]]: ...
    def uid(self, command: str, *args: str) -> tuple[str, list[object]]: ...
    def logout(self) -> object: ...


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return "".join(extractor.parts)


def clean_text(text: str) -> str:
    """Drop links and invisible padding, keeping one paragraph per line."""
    text = _INVISIBLE_RE.sub("", text)
    text = _BRACKETED_LINK_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    lines = (_SPACES_RE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_message(message_id: int, received_at: datetime, raw: bytes) -> Newsletter:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    body = message.get_body(preferencelist=("plain", "html"))
    content = body.get_content() if body is not None else ""
    if body is not None and body.get_content_subtype() == "html":
        content = _html_to_text(content)
    list_id = str(message.get("List-Id", "")).strip().strip("<>")
    return Newsletter(
        # Matches the message id in Gmail's API and web URLs.
        id=f"{message_id:x}",
        sender=email.utils.parseaddr(str(message.get("From", "")))[1],
        publication=list_id or None,
        subject=str(message.get("Subject", "")).replace("\x00", "").strip(),
        received_at=received_at,
        body_text=clean_text(content),
    )


def _split_long(paragraph: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END_RE.split(paragraph):
        while len(sentence) > MAX_PASSAGE_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:MAX_PASSAGE_CHARS])
            sentence = sentence[MAX_PASSAGE_CHARS:]
        if current and len(current) + 1 + len(sentence) > MAX_PASSAGE_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def passage_chunks(body_text: str) -> list[str]:
    """A cleaned body cut into pieces short enough for FinBERT: one per paragraph, long ones split."""
    return [chunk for paragraph in body_text.splitlines() for chunk in _split_long(paragraph)]


def ticker_passages(body_text: str, watchlist: Watchlist) -> list[Passage]:
    """The passages of a cleaned newsletter body that mention at least one ticker."""
    passages = []
    for index, chunk in enumerate(passage_chunks(body_text)):
        tickers = extract_tickers(chunk, watchlist)
        if tickers:
            passages.append(Passage(index, chunk, tickers))
    return passages


def _fetched_message(uid: str, data: list[object]) -> Newsletter:
    metadata = b" ".join(
        bytes(item[0]) if isinstance(item, tuple) else bytes(item)
        for item in data
        if isinstance(item, (tuple, bytes))
    )
    raw = next((bytes(item[1]) for item in data if isinstance(item, tuple) and len(item) > 1), None)
    message_id = _MSGID_RE.search(metadata)
    internal_date = _INTERNALDATE_RE.search(metadata)
    received = imaplib.Internaldate2tuple(internal_date.group(0)) if internal_date else None
    if raw is None or message_id is None or received is None:
        raise LoaderError(f"Unexpected IMAP FETCH response for message UID {uid}")
    received_at = datetime.fromtimestamp(time.mktime(received), tz=timezone.utc)
    return parse_message(int(message_id.group(1)), received_at, raw)


def _connect(host: str) -> ImapConnection:
    # Without an explicit context imaplib does not check the server's certificate.
    try:
        return imaplib.IMAP4_SSL(host, ssl_context=ssl.create_default_context(), timeout=_IMAP_TIMEOUT_SECONDS)
    except OSError as exc:
        raise LoaderError(f"Cannot connect to {host}: {exc}") from None


def fetch_newsletters(
    address: str,
    app_password: str,
    label: str,
    since: date,
    connect: Callable[[str], ImapConnection] = _connect,
) -> Iterator[Newsletter]:
    """Yield every message under a Gmail label received on or after `since`, without marking it read."""
    if _CONTROL_CHARACTER_RE.search(label):
        raise LoaderError(f"Gmail label {label!r} contains a control character")
    connection = connect(GMAIL_IMAP_HOST)
    try:
        try:
            connection.login(address, app_password)
        except imaplib.IMAP4.error as exc:
            detail = exc.args[0] if exc.args else ""
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise LoaderError(f"Gmail IMAP login failed for {address}: {detail}") from None
        mailbox = '"' + label.replace("\\", "\\\\").replace('"', '\\"') + '"'
        status, _ = connection.select(mailbox, readonly=True)
        if status != "OK":
            raise LoaderError(f"Could not open Gmail label {label!r}")
        since_text = f"{since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}"
        status, found = connection.uid("SEARCH", "SINCE", since_text)
        if status != "OK":
            raise LoaderError(f"Gmail IMAP search failed for label {label!r}")
        first = found[0] if found else b""
        for uid in (bytes(first) if isinstance(first, bytes) else b"").decode("ascii").split():
            status, data = connection.uid("FETCH", uid, "(X-GM-MSGID INTERNALDATE BODY.PEEK[])")
            if status != "OK":
                raise LoaderError(f"Gmail IMAP fetch failed for message UID {uid}")
            yield _fetched_message(uid, data)
    except (imaplib.IMAP4.abort, OSError) as exc:
        raise LoaderError(f"Gmail IMAP connection failed: {exc}") from None
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
