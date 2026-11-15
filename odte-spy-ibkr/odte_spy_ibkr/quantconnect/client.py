from __future__ import annotations

import base64
import hashlib
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

QC_API_BASE = "https://www.quantconnect.com/api/v2"

QC_DOCS_SUMMARY = (
    "QuantConnect historical files are purchased via the LEAN CLI `lean data download` "
    "(Dataset Market, QCC per file, paid organization tier required). "
    "The REST API authenticates via hashed UserId+API token but does not stream arbitrary "
    "minute history; see https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-data-download"
)


def qc_auth_headers(*, user_id: str, api_token: str) -> dict[str, str]:
    timestamp = f"{int(time.time())}"
    time_stamped = f"{api_token}:{timestamp}".encode("utf-8")
    hashed = hashlib.sha256(time_stamped).hexdigest()
    auth = base64.b64encode(f"{user_id}:{hashed}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {auth}", "Timestamp": timestamp}


class QuantConnectClient:
    """https://www.quantconnect.com/docs/v2/our-platform/api-reference/authentication"""

    def __init__(
        self,
        *,
        user_id: str,
        api_token: str,
        base_url: str = QC_API_BASE,
        timeout_sec: float = 30.0,
    ) -> None:
        self._user_id = user_id.strip()
        self._token = api_token.strip()
        self._base = base_url.rstrip("/")
        self._timeout = timeout_sec

    def _headers(self) -> dict[str, str]:
        if not (self._user_id and self._token):
            raise RuntimeError(
                "QUANTCONNECT_USER_ID and QUANTCONNECT_API_TOKEN are required "
                "(email from https://www.quantconnect.com/account)."
            )
        return qc_auth_headers(user_id=self._user_id, api_token=self._token)

    def authenticate(self) -> dict[str, Any]:
        url = f"{self._base}/authenticate"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(url, headers=self._headers(), json={})
            if resp.status_code == 401:
                raise RuntimeError(
                    "QuantConnect credentials rejected (401). Check QUANTCONNECT_USER_ID / "
                    "QUANTCONNECT_API_TOKEN and system clock."
                )
            resp.raise_for_status()
            payload = resp.json()
        if not payload.get("success"):
            raise RuntimeError(f"QuantConnect authenticate failed: {payload!r}")
        return payload