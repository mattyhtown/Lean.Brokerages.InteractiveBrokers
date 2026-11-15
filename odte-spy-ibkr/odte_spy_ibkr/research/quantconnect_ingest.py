from __future__ import annotations

import json
import logging
from datetime import date, time as dt_time
from pathlib import Path
from typing import Any

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.quantconnect.client import QC_DOCS_SUMMARY, QuantConnectClient
from odte_spy_ibkr.quantconnect.lean_download import lean_cli_available, run_lean_data_download
from odte_spy_ibkr.quantconnect.lean_reader import (
    chain_rows_at_times,
    load_equity_minute_bars,
    resolve_lean_data_root,
)
from odte_spy_ibkr.research.alpaca_ingest import save_json
from odte_spy_ibkr.research.chain_io import save_chain_snapshot
from odte_spy_ibkr.research.features import SIGNAL_TIME
from odte_spy_ibkr.research.paths import QUANTCONNECT_ROOT, chain_path

logger = logging.getLogger(__name__)

CLOSE_TIME = dt_time(16, 0)

QUANTCONNECT_INGEST_NOTES = (
    "QuantConnect does not expose bulk historical minute bars over REST. "
    "This ingest authenticates via /api/v2/authenticate, optionally runs "
    "`lean data download` (Dataset Market, QCC per file, paid org), then "
    "imports LEAN on-disk zips into data/research/quantconnect/minute/ "
    "and unified data/research/chains/SPY/. "
    "Set QUANTCONNECT_ALLOW_PAID_DOWNLOAD=true to enable CLI purchases."
)


def has_quantconnect_credentials(settings: Settings) -> bool:
    return bool(settings.quantconnect_user_id.strip() and settings.quantconnect_api_token.strip())


def require_credentials(settings: Settings) -> None:
    if not has_quantconnect_credentials(settings):
        raise RuntimeError(
            "QUANTCONNECT_USER_ID and QUANTCONNECT_API_TOKEN are required for real-data ingest "
            "(request from https://www.quantconnect.com/account)."
        )


def _maybe_download(
    settings: Settings,
    *,
    start: date,
    end: date,
    include_options: bool,
) -> list[dict[str, Any]]:
    if not settings.quantconnect_allow_paid_download:
        logger.info(
            "Skipping lean data download (QUANTCONNECT_ALLOW_PAID_DOWNLOAD=false). "
            "Importing existing files under %s",
            resolve_lean_data_root(settings.quantconnect_lean_data_dir),
        )
        return []
    logs: list[dict[str, Any]] = []
    ticker = settings.underlying.upper()
    uid = settings.quantconnect_user_id.strip()
    token = settings.quantconnect_api_token.strip()
    try:
        logs.append(
            run_lean_data_download(
                user_id=uid,
                api_token=token,
                dataset=settings.quantconnect_equity_dataset,
                ticker=ticker,
                start=start,
                end=end,
                resolution="Minute",
                data_type="Trade",
                security_type="Equity",
                market="usa",
                lean_data_dir=settings.quantconnect_lean_workspace or None,
            )
        )
    except RuntimeError as exc:
        logger.warning("Equity lean download: %s", exc)
        logs.append({"equity_download_error": str(exc)})
    if include_options:
        try:
            logs.append(
                run_lean_data_download(
                    user_id=uid,
                    api_token=token,
                    dataset=settings.quantconnect_option_dataset,
                    ticker=ticker,
                    start=start,
                    end=end,
                    resolution="Minute",
                    data_type="Quote",
                    security_type="Option",
                    market="usa",
                    lean_data_dir=settings.quantconnect_lean_workspace or None,
                )
            )
        except RuntimeError as exc:
            logger.warning("Options lean download: %s", exc)
            logs.append({"options_download_error": str(exc)})
    return logs


def ingest_range(
    settings: Settings,
    *,
    start: date,
    end: date,
    download: bool | None = None,
) -> dict[str, Any]:
    """Import SPY equity minutes + 0DTE option quotes from local LEAN data tree."""
    require_credentials(settings)
    client = QuantConnectClient(
        user_id=settings.quantconnect_user_id,
        api_token=settings.quantconnect_api_token,
        base_url=settings.quantconnect_api_base,
    )
    auth = client.authenticate()

    QUANTCONNECT_ROOT.mkdir(parents=True, exist_ok=True)
    lean_root = resolve_lean_data_root(settings.quantconnect_lean_data_dir)
    lean_root.mkdir(parents=True, exist_ok=True)

    do_download = (
        settings.quantconnect_allow_paid_download if download is None else download
    )
    settings_copy = settings.model_copy(update={"quantconnect_allow_paid_download": do_download})
    download_log = _maybe_download(settings_copy, start=start, end=end, include_options=True)

    stats: dict[str, Any] = {
        "source": "quantconnect",
        "authenticated": bool(auth.get("success")),
        "lean_data_root": str(lean_root),
        "lean_cli": lean_cli_available(),
        "days_minute": 0,
        "days_minute_skipped": 0,
        "days_chain": 0,
        "days_chain_empty": 0,
        "docs": QC_DOCS_SUMMARY,
        "download_log": download_log,
    }

    daily_closes: list[dict[str, Any]] = []
    symbol = settings.underlying.upper()

    for day in iter_trading_days(start, end):
        minute_path = QUANTCONNECT_ROOT / "minute" / f"{day.isoformat()}.json"
        if minute_path.is_file():
            stats["days_minute_skipped"] += 1
            minutes = json.loads(minute_path.read_text(encoding="utf-8"))
        else:
            minutes = load_equity_minute_bars(lean_root, symbol=symbol.lower(), day=day)
            if minutes:
                save_json(minute_path, minutes)
                stats["days_minute"] += 1
                daily_closes.append({"t": f"{day.isoformat()}T16:00:00-05:00", "c": minutes[-1]["c"]})
            else:
                logger.debug("No LEAN equity minute file for %s", day)

        chain_file = chain_path(day, snapshot="signal")
        if chain_file.is_file():
            continue
        signal_rows, close_rows = chain_rows_at_times(
            lean_root,
            symbol=symbol.lower(),
            day=day,
            signal_time=SIGNAL_TIME,
            close_time=CLOSE_TIME,
        )
        if signal_rows:
            save_chain_snapshot(signal_rows, day, snapshot="signal", source="quantconnect")
            if close_rows:
                save_chain_snapshot(close_rows, day, snapshot="close", source="quantconnect")
            stats["days_chain"] += 1
        else:
            stats["days_chain_empty"] += 1

    if daily_closes:
        save_json(QUANTCONNECT_ROOT / "spy_daily.json", daily_closes)

    notes = {
        "summary": QUANTCONNECT_INGEST_NOTES,
        "stats": stats,
        "limits": {
            "rest_api": "No arbitrary historical stream; authenticate + org APIs only.",
            "lean_cli": "Paid organization tier; per-file QCC on Dataset Market.",
            "security_master": "US Equity Security Master subscription often required.",
            "reference": "https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-data-download",
        },
    }
    save_json(QUANTCONNECT_ROOT / "ingest_notes.json", notes)
    return stats