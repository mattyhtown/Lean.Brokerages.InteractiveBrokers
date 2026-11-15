from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Mapping
import urllib.parse

from research_data.http import HttpCallable, LoaderError, require_secure_url, send

_KEY_ENVS = ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY")
_WRITE_BATCH_ROWS = 500
_READ_PAGE_ROWS = 1000


@dataclass(frozen=True)
class SupabaseClient:
    """Minimal client for the Supabase Data API (PostgREST)."""

    url: str
    key: str
    http: HttpCallable = send
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, http: HttpCallable = send) -> SupabaseClient:
        source = os.environ if env is None else env
        url = source.get("SUPABASE_URL", "").strip()
        if not url:
            raise LoaderError("Missing SUPABASE_URL (for example https://<project-ref>.supabase.co)")
        key = next((source[name].strip() for name in _KEY_ENVS if source.get(name, "").strip()), "")
        if not key:
            raise LoaderError(f"Missing Supabase secret key (set {' or '.join(_KEY_ENVS)})")
        return cls(require_secure_url(url), key, http)

    def _request(
        self,
        method: str,
        table: str,
        query: Mapping[str, str],
        payload: object | None = None,
        prefer: str | None = None,
    ) -> object:
        url = f"{self.url}/rest/v1/{table}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if prefer:
            headers["Prefer"] = prefer
        response = self.http(method, url, headers, body, self.timeout_seconds)
        if not response.body:
            return None
        return json.loads(response.body)

    def select(
        self,
        table: str,
        columns: str,
        order: str,
        filters: Mapping[str, str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        """Read rows in a stable order, paging until `limit` rows or the end of the table."""
        rows: list[dict[str, object]] = []
        while limit is None or len(rows) < limit:
            page_size = _READ_PAGE_ROWS if limit is None else min(_READ_PAGE_ROWS, limit - len(rows))
            query = {
                **(filters or {}),
                "select": columns,
                "order": order,
                "limit": str(page_size),
                "offset": str(len(rows)),
            }
            page = self._request("GET", table, query)
            if not isinstance(page, list):
                raise LoaderError(f"Unexpected response when reading {table}")
            rows.extend(page)
            if len(page) < page_size:
                break
        return rows

    def upsert(
        self,
        table: str,
        rows: list[dict[str, object]],
        on_conflict: str,
        ignore_duplicates: bool = False,
    ) -> None:
        resolution = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
        for start in range(0, len(rows), _WRITE_BATCH_ROWS):
            self._request(
                "POST",
                table,
                {"on_conflict": on_conflict},
                rows[start : start + _WRITE_BATCH_ROWS],
                prefer=f"resolution={resolution},return=minimal",
            )

    def update(self, table: str, filters: Mapping[str, str], values: dict[str, object]) -> None:
        if not filters:
            raise LoaderError(f"Refusing to update every row of {table}")
        self._request("PATCH", table, filters, values, prefer="return=minimal")
