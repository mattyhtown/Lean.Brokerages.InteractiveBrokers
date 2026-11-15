from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


def lean_cli_available() -> bool:
    return shutil.which("lean") is not None


def run_lean_data_download(
    *,
    user_id: str,
    api_token: str,
    dataset: str,
    ticker: str,
    start: date,
    end: date,
    resolution: str = "Minute",
    data_type: str = "Trade",
    security_type: str = "Equity",
    market: str = "usa",
    auto_confirm: bool = True,
    lean_data_dir: str | None = None,
) -> dict[str, Any]:
    """
    Non-interactive ``lean data download``.

    Charges organization QCC per file on QuantConnect's Dataset Market (paid tier).
    """
    if not lean_cli_available():
        raise RuntimeError(
            "LEAN CLI (`lean`) not found on PATH. Install from "
            "https://www.quantconnect.com/docs/v2/lean-cli/installation "
            "or set QUANTCONNECT_LEAN_DATA_DIR to pre-downloaded LEAN files."
        )
    cmd = [
        "lean",
        "data",
        "download",
        "--dataset",
        dataset,
        "--data-type",
        data_type,
        "--ticker",
        ticker,
        "--resolution",
        resolution,
        "--security-type",
        security_type,
        "--market",
        market,
        "--start",
        start.strftime("%Y%m%d"),
        "--end",
        end.strftime("%Y%m%d"),
    ]
    if auto_confirm:
        cmd.append("--yes")
    logger.info("Running: %s", " ".join(cmd))
    env = {
        **os.environ,
        "QC_USER_ID": user_id,
        "QC_API_TOKEN": api_token,
    }
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=lean_data_dir or None,
        env=env,
        check=False,
    )
    out = {
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-4000:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-4000:] if proc.stderr else "",
        "dataset": dataset,
        "ticker": ticker,
    }
    if proc.returncode != 0:
        raise RuntimeError(
            f"lean data download failed (exit {proc.returncode}). "
            f"Dataset Market downloads require a paid QC organization, active dataset "
            f"subscriptions (e.g. US Equity Security Master), and QCC balance. "
            f"stderr: {proc.stderr[:500]}"
        )
    return out