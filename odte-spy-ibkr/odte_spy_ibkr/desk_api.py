"""Live ODTE Desk state API — read-only snapshot for the 3D UI.

Serves GET /desk/state and continuously writes data/desk/signal_state.json.
Never enables ENABLE_ORDERS. IBKR is optional; without it the loop still
refreshes gates, journal Sharpe, RTH/flatten clocks, and degraded provider status.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.providers.aggregate import MarketContextAggregator
from odte_spy_ibkr.providers.registry import build_providers
from odte_spy_ibkr.risk import RiskLimits, RiskState, check_new_entry, is_rth, should_flatten
from odte_spy_ibkr.risk.sharpe_gate import allow_new_entries, sharpe_gate_state
from odte_spy_ibkr.strategy.flow_gate import GateDecision, evaluate_flow_gate
from odte_spy_ibkr.strategy.macro_gate import evaluate_macro_gate

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

_STATE_LOCK = threading.Lock()
_LATEST: dict[str, Any] = {}
_TICK = 0


def _desk_paths(settings: Settings) -> tuple[Path, Path]:
    root = Path(settings.journal_path).resolve().parent.parent
    desk_dir = root / "desk"
    desk_dir.mkdir(parents=True, exist_ok=True)
    return desk_dir / "signal_state.json", Path(settings.journal_path)


def _gate(
    gid: str,
    label: str,
    status: str,
    value: float,
    threshold: float,
    detail: str,
) -> dict[str, Any]:
    return {
        "id": gid,
        "label": label,
        "status": status,
        "value": value,
        "threshold": threshold,
        "detail": detail,
    }


def _status_from_bool(ok: bool, *, watch: bool = False) -> str:
    if not ok:
        return "block"
    if watch:
        return "watch"
    return "pass"


def build_desk_state(settings: Settings) -> dict[str, Any]:
    """Assemble a DeskState-compatible dict from live settings + journal + providers."""
    global _TICK
    _TICK += 1
    now_et = datetime.now(tz=ET)
    as_of = now_et.isoformat(timespec="seconds")

    # Force UI-side safety: this API never advertises order placement capability for the desk.
    # Engine-reported flags are still included truthfully under safety.* so the HUD is honest.
    engine_enable = bool(settings.enable_orders)
    engine_read_only = bool(settings.read_only)
    engine_live = bool(settings.live_trading)

    signal_path, journal_path = _desk_paths(settings)

    # Sharpe from journal (live file)
    sharpe_state = sharpe_gate_state(
        journal_path,
        window=settings.sharpe_window_days,
        min_obs=settings.sharpe_min_obs,
    )
    sharpe_ok, sharpe_reason = allow_new_entries(
        journal_path,
        window=settings.sharpe_window_days,
        min_obs=settings.sharpe_min_obs,
        sharpe_min=settings.sharpe_min,
    )
    sharpe_val = sharpe_state.rolling_sharpe
    if sharpe_val is None:
        sharpe_gate = _gate(
            "sharpe",
            "Rolling Sharpe",
            "watch",
            float(sharpe_state.observations),
            float(settings.sharpe_min_obs),
            f"insufficient_journal ({sharpe_reason})",
        )
    else:
        sharpe_gate = _gate(
            "sharpe",
            "Rolling Sharpe",
            _status_from_bool(sharpe_ok),
            round(float(sharpe_val), 3),
            float(settings.sharpe_min),
            sharpe_reason,
        )

    # Risk / flatten clocks — always live from wall clock
    limits = RiskLimits(
        max_contracts=settings.max_contracts,
        max_daily_loss=settings.max_daily_loss,
        entry_cutoff=settings.entry_cutoff,
        flatten_time=settings.flatten_time,
        min_minutes_after_open=settings.min_minutes_after_open,
    )
    risk_decision, risk_reason = check_new_entry(limits, RiskState())
    flatten_now = should_flatten(limits, now_et)
    rth = is_rth(now_et)
    t = now_et.timetz().replace(tzinfo=None)
    flat = settings.flatten_time
    minutes_to_flatten = max(
        0.0,
        (flat.hour * 60 + flat.minute) - (t.hour * 60 + t.minute),
    )

    degraded: list[str] = []
    ctx = None
    spot = 0.0
    net_bias = 0.0
    call_prem = 0.0
    put_prem = 0.0
    call_vol = 0
    put_vol = 0
    signals: list[dict[str, Any]] = []
    headlines: list[dict[str, Any]] = []
    macro_intensity = 0.0
    macro_risk_off = False
    macro_blackout = False
    flow_strength = 0.0
    flow_detail = "no_context"
    flow_status = "watch"

    try:
        providers = build_providers(settings, None)
        if not providers:
            degraded.append("no_providers")
        else:
            aggregator = MarketContextAggregator(providers, underlying=settings.underlying)
            ctx = aggregator.build()
    except Exception as exc:  # noqa: BLE001
        degraded.append(f"providers:{exc.__class__.__name__}")
        logger.warning("Provider aggregation degraded: %s", exc)

    if ctx is not None:
        spot = float(ctx.spot or 0.0)
        net_bias = float(ctx.net_call_bias or 0.0)
        macro_intensity = abs(float(ctx.macro_headline_score or 0.0))
        macro_risk_off = bool(ctx.macro_risk_off)
        macro_blackout = bool(ctx.macro_event_blackout)
        skew = getattr(ctx, "chain_skew", None)
        if skew is not None:
            call_prem = float(getattr(skew, "call_premium_mid", 0.0) or 0.0)
            put_prem = float(getattr(skew, "put_premium_mid", 0.0) or 0.0)
            call_vol = int(getattr(skew, "call_volume", 0) or 0)
            put_vol = int(getattr(skew, "put_volume", 0) or 0)

        flow = evaluate_flow_gate(settings, ctx)
        flow_strength = float(flow.strength)
        flow_detail = flow.reason
        if flow.decision == GateDecision.BLOCK:
            flow_status = "block"
        elif flow.decision == GateDecision.LOG_ONLY:
            flow_status = "watch"
        else:
            flow_status = "pass"

        macro = evaluate_macro_gate(settings, ctx)
        if macro.blocked:
            macro_status = "block"
            macro_detail = macro.reason
        elif macro.decision.value == "downsize":
            macro_status = "watch"
            macro_detail = macro.reason
        else:
            macro_status = "pass"
            macro_detail = macro.reason

        side_hint = "put_credit" if net_bias >= 0 else "call_credit"
        signals.append(
            {
                "id": f"live-{_TICK}",
                "side": side_hint,
                "summary": (
                    f"Live tick #{_TICK} · spot {spot:.2f} · bias {net_bias:+.2f}"
                    if spot
                    else f"Live tick #{_TICK} · spot unavailable · bias {net_bias:+.2f}"
                ),
                "strength": flow_strength,
                "blocked": flow.decision == GateDecision.BLOCK or macro.blocked
                or risk_decision.value == "block"
                or not sharpe_ok,
                "reason": f"{flow.reason};{macro.reason};{risk_reason};{sharpe_reason}",
            }
        )
        for pid, note in list(ctx.provider_notes.items())[:4]:
            headlines.append(
                {
                    "source": str(pid),
                    "title": str(note)[:160] or "provider note",
                    "score": macro_intensity,
                }
            )
    else:
        macro_status = "watch"
        macro_detail = "providers_degraded"
        degraded.append("no_market_context")
        signals.append(
            {
                "id": f"degraded-{_TICK}",
                "side": "none",
                "summary": f"Degraded live tick #{_TICK} — providers/IBKR unavailable",
                "strength": 0.0,
                "blocked": True,
                "reason": ",".join(degraded) or "degraded",
            }
        )

    if not headlines:
        headlines.append(
            {
                "source": "desk_api",
                "title": (
                    "Live desk loop · "
                    + ("RTH open" if rth else "outside RTH")
                    + (f" · degraded: {','.join(degraded)}" if degraded else "")
                ),
                "score": 0.0,
            }
        )

    risk_ok = risk_decision.value == "allow"
    risk_gate = _gate(
        "risk",
        "Risk limits",
        _status_from_bool(risk_ok, watch=not rth),
        0.0,
        float(settings.max_contracts),
        risk_reason if rth else "outside_rth",
    )
    macro_gate = _gate(
        "macro",
        "Macro gate",
        macro_status,
        round(macro_intensity, 3),
        float(settings.macro_headline_intensity_block),
        macro_detail,
    )
    flow_gate = _gate(
        "flow",
        "UW flow gate",
        flow_status,
        round(flow_strength, 3),
        float(settings.uw_min_strength),
        f"{flow_detail} · mode={settings.uw_mode.value}",
    )

    # Journal-derived backtest-ish summary
    returns_n = sharpe_state.observations
    backtest = {
        "windowDays": settings.sharpe_window_days,
        "trades": returns_n,
        "winRate": 0.0,
        "rollingSharpe": float(sharpe_val) if sharpe_val is not None else 0.0,
        "maxDrawdown": 0.0,
        "avgCredit": settings.min_credit,
        "note": (
            f"Live journal ({journal_path.name}) · obs={returns_n} · tick={_TICK}"
            + (f" · {','.join(degraded)}" if degraded else "")
        ),
    }
    if journal_path.is_file():
        wins = 0
        total = 0
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if float(row.get("pnl", 0.0)) > 0:
                wins += 1
        if total:
            backtest["trades"] = total
            backtest["winRate"] = wins / total

    state = {
        "asOf": as_of,
        "brand": "ODTE Desk",
        "symbol": settings.underlying,
        "mode": "strict",
        "safety": {
            "liveTrading": engine_live,
            "readOnly": engine_read_only,
            "enableOrders": engine_enable,
            "ordersLockedReason": (
                "Desk UI cannot place orders"
                + (
                    f" · engine ENABLE_ORDERS={engine_enable} READ_ONLY={engine_read_only}"
                )
            ),
            "killSwitchArmed": True,
            "killSwitchLabel": "HARD KILL — flatten + block entries",
            "uiOrdersLocked": True,
        },
        "spot": spot,
        "gates": {
            "sharpe": sharpe_gate,
            "macro": macro_gate,
            "flow": flow_gate,
            "risk": risk_gate,
        },
        "flatten": {
            "flattenTimeEt": settings.flatten_time.strftime("%H:%M"),
            "entryCutoffEt": settings.entry_cutoff.strftime("%H:%M"),
            "shouldFlatten": flatten_now,
            "minutesToFlatten": int(minutes_to_flatten),
            "rth": rth,
        },
        "positions": [],
        "signals": signals,
        "chainSkew": {
            "netCallBias": net_bias,
            "callPremiumMid": call_prem,
            "putPremiumMid": put_prem,
            "callVolume": call_vol,
            "putVolume": put_vol,
            "note": (
                "Live skew from providers"
                if ctx is not None
                else "Skew unavailable — degraded live loop"
            ),
        },
        "macro": {
            "riskOff": macro_risk_off,
            "headlineIntensity": macro_intensity,
            "blackout": macro_blackout,
            "headlines": headlines,
        },
        "backtest": backtest,
        "stations": [
            {"id": "signals", "title": "Signals", "subtitle": "Live propose · read-only"},
            {"id": "risk", "title": "Risk", "subtitle": "Kill · flatten · limits"},
            {"id": "backtest", "title": "Backtest", "subtitle": "Journal Sharpe"},
            {"id": "macro", "title": "Macro", "subtitle": "News + blackout gate"},
            {"id": "chain", "title": "Chain skew", "subtitle": "Net call bias"},
        ],
        "source": "engine",
        "degraded": degraded,
        "tick": _TICK,
        "signalStatePath": str(signal_path),
    }

    signal_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def _cors(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


class DeskHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("desk_api %s", fmt % args)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        _cors(self)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/desk/state", "/health", "/"}:
            with _STATE_LOCK:
                payload = dict(_LATEST) if _LATEST else {"status": "warming"}
            if path == "/health":
                body = json.dumps(
                    {
                        "ok": True,
                        "tick": payload.get("tick"),
                        "asOf": payload.get("asOf"),
                        "source": payload.get("source"),
                    }
                ).encode()
            else:
                if path == "/" and "brand" not in payload:
                    body = json.dumps({"service": "odte-desk-api", "paths": ["/desk/state", "/health"]}).encode()
                else:
                    body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            _cors(self)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        _cors(self)
        self.end_headers()


def _refresh_loop(settings: Settings, interval: float, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            state = build_desk_state(settings)
            with _STATE_LOCK:
                _LATEST.clear()
                _LATEST.update(state)
            logger.info(
                "desk state tick=%s asOf=%s degraded=%s spot=%s",
                state.get("tick"),
                state.get("asOf"),
                state.get("degraded"),
                state.get("spot"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("desk state refresh failed")
        stop.wait(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="ODTE Desk live state API (orders never enabled)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--interval", type=float, default=5.0, help="Refresh seconds")
    parser.add_argument("--once", action="store_true", help="Build one snapshot and exit")
    args = parser.parse_args()

    # Hard safety for this process — never flip orders on from the desk API.
    os.environ["ENABLE_ORDERS"] = "false"

    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    logger.info(
        "Desk API starting ENABLE_ORDERS=%s READ_ONLY=%s LIVE_TRADING=%s providers=%s",
        settings.enable_orders,
        settings.read_only,
        settings.live_trading,
        settings.providers_enabled,
    )

    state = build_desk_state(settings)
    with _STATE_LOCK:
        _LATEST.clear()
        _LATEST.update(state)

    if args.once:
        print(json.dumps({"ok": True, "tick": state["tick"], "asOf": state["asOf"], "path": state["signalStatePath"]}))
        return

    stop = threading.Event()
    worker = threading.Thread(target=_refresh_loop, args=(settings, args.interval, stop), daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.host, args.port), DeskHandler)
    logger.info("Serving desk state on http://%s:%s/desk/state", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        server.shutdown()


if __name__ == "__main__":
    main()
