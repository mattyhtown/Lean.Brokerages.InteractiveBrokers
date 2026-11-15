from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import ipaddress
from typing import BinaryIO, Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request


class LoaderError(Exception):
    pass


class HttpStatusError(LoaderError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    # Header names are lower-cased.
    headers: Mapping[str, str] = field(default_factory=dict)


HttpCallable = Callable[[str, str, Mapping[str, str], bytes | None, int], HttpResponse]


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    lowered = hostname.lower().strip("[]")
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return lowered == "localhost"


def require_secure_url(url: str) -> str:
    """Credentials are sent with every request, so only HTTPS (or HTTP on loopback) is accepted."""
    cleaned = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if not parsed.hostname or parsed.scheme not in ("https", "http"):
        raise LoaderError(f"Invalid URL: {url!r}")
    if "@" in parsed.netloc:
        # In https://trusted.example@other.example the request goes to other.example.
        raise LoaderError(f"Refusing URL with a user name in it: {url!r}")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise LoaderError(f"Refusing non-HTTPS URL: {url!r}")
    return cleaned


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the credentials to whatever host it names, so none are followed."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


def send(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: int,
) -> HttpResponse:
    require_secure_url(url)
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    parsed = urllib.parse.urlparse(url)
    try:
        with _OPENER.open(request, timeout=timeout_seconds) as response:
            response_headers = {name.lower(): value for name, value in response.headers.items()}
            return HttpResponse(response.status, response.read(), response_headers)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()[:500]
        raise HttpStatusError(exc.code, f"HTTP {exc.code} from {parsed.netloc}{parsed.path}: {detail}") from None
    except urllib.error.URLError as exc:
        raise LoaderError(f"Request to {parsed.netloc}{parsed.path} failed: {exc.reason}") from None


_PUBLIC_USER_AGENT = "research-data/0.1"
_READ_BYTES = 1 << 20


def _public_host(url: str) -> str:
    """The host of an address that `fetch_public` may read. Errors name no more than the host,
    because the rest of a private feed's address is its password."""
    parsed = urllib.parse.urlparse(url.strip())
    host = parsed.hostname
    if not host or parsed.scheme not in ("https", "http") or "@" in parsed.netloc:
        raise LoaderError("An address is not a plain web address")
    if parsed.scheme == "http" and not _is_loopback(host):
        raise LoaderError(f"The address at {host} is not HTTPS")
    return host


class _SecureRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to an address `fetch_public` would have accepted in the first place."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _public_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PUBLIC_OPENER = urllib.request.build_opener(_SecureRedirects)

PublicFetch = Callable[[str, int, BinaryIO | None], HttpResponse]


def fetch_public(url: str, max_bytes: int, sink: BinaryIO | None = None, timeout_seconds: int = 60) -> HttpResponse:
    """Read a public address that takes no credentials, following redirects.

    The body is returned, or written to `sink` when one is given. A body longer than
    `max_bytes` is an error rather than being cut short.
    """
    host = _public_host(url)
    request = urllib.request.Request(url.strip(), headers={"User-Agent": _PUBLIC_USER_AGENT})
    received = 0
    chunks: list[bytes] = []
    try:
        with _PUBLIC_OPENER.open(request, timeout=timeout_seconds) as response:
            while chunk := response.read(_READ_BYTES):
                received += len(chunk)
                if received > max_bytes:
                    raise LoaderError(f"The file at {host} is larger than {max_bytes // 1_000_000} MB")
                if sink is None:
                    chunks.append(chunk)
                else:
                    sink.write(chunk)
            headers = {name.lower(): value for name, value in response.headers.items()}
            # A connection that drops partway ends the read without an error.
            if headers.get("content-length", "").isdigit() and received != int(headers["content-length"]):
                raise LoaderError(f"Download from {host} stopped after {received} of {headers['content-length']} bytes")
            return HttpResponse(response.status, b"".join(chunks), headers)
    except urllib.error.HTTPError as exc:
        raise HttpStatusError(exc.code, f"HTTP {exc.code} from {host}") from None
    except urllib.error.URLError as exc:
        raise LoaderError(f"Request to {host} failed: {exc.reason}") from None
    except (OSError, http.client.HTTPException) as exc:
        raise LoaderError(f"Download from {host} failed: {exc or type(exc).__name__}") from None
