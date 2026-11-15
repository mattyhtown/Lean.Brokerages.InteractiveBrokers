from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
from typing import Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request


class LoaderError(Exception):
    pass


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
    if not parsed.netloc or parsed.scheme not in ("https", "http"):
        raise LoaderError(f"Invalid URL: {url!r}")
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
        raise LoaderError(f"HTTP {exc.code} from {parsed.netloc}{parsed.path}: {detail}") from None
    except urllib.error.URLError as exc:
        raise LoaderError(f"Request to {parsed.netloc}{parsed.path} failed: {exc.reason}") from None
