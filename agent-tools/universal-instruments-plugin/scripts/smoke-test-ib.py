#!/usr/bin/env python3
"""Read-only smoke test for ib-lean-bridge against a local TWS or IB Gateway.

Exercises ping, account summary, positions, and contract details / historical bars /
snapshot quotes for EURUSD (forex), front-month ES (CME future), and a US Treasury
bond. Optionally runs an IB what-if preview (--preview), which sends nothing.
Never calls place_order or cancel_order.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any

BRIDGE_DIR = Path(__file__).resolve().parent.parent / "mcp" / "ib-lean-bridge"
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

ENTITLEMENT_CODES = {"162", "354", "10089", "10167", "10168"}
ERROR_CODE_RE = re.compile(r"^(\d+):")


def _check_tcp(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP port {port} is open on {host}"
    except OSError as exc:
        return False, f"Cannot reach {host}:{port} ({exc})"


def _find_entitlement_messages(messages: list[str]) -> list[str]:
    hits = []
    for msg in messages:
        match = ERROR_CODE_RE.match(msg.strip())
        if match and match.group(1) in ENTITLEMENT_CODES:
            hits.append(msg)
    return hits


def _pick_front_expiry(contracts: list[dict[str, Any]]) -> str:
    today_ym = datetime.date.today().strftime("%Y%m")
    expiries = sorted(
        {
            str(c.get("lastTradeDateOrContractMonth") or "").strip()
            for c in contracts
            if c.get("lastTradeDateOrContractMonth")
        }
    )
    upcoming = [exp for exp in expiries if exp[:6] >= today_ym]
    return upcoming[0] if upcoming else (expiries[0] if expiries else "")


async def _run_step(name: str, coro: Any) -> dict[str, Any]:
    try:
        raw = await coro
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        return {"step": name, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    messages = data.get("messages", []) if isinstance(data, dict) else []
    entitlement = _find_entitlement_messages(messages)
    status = "ENTITLEMENT_NEEDED" if entitlement else "PASS"
    return {
        "step": name,
        "status": status,
        "entitlementMessages": entitlement,
        "messages": messages,
        "data": data,
    }


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    import server  # imported lazily after sys.path setup

    if args.host:
        server.IB_HOST = args.host
    if args.port:
        server.IB_PORT = args.port
    if args.client_id is not None:
        server.IB_CLIENT_ID = args.client_id
    if args.account:
        server.IB_ACCOUNT = args.account

    ping_data = json.loads(server.ping())
    reachable, tcp_msg = _check_tcp(server.IB_HOST, server.IB_PORT)
    report: dict[str, Any] = {
        "ping": ping_data,
        "tcp": {"ok": reachable, "detail": tcp_msg},
        "steps": [],
    }
    if not reachable:
        report["hint"] = (
            "Start TWS (paper port 7497) or IB Gateway (paper port 4002), enable "
            "'Enable ActiveX and Socket Clients', and add 127.0.0.1 to Trusted IPs."
        )
        return report

    steps = report["steps"]
    steps.append(await _run_step("account_summary", server.get_account_summary()))
    steps.append(await _run_step("positions", server.get_positions()))

    # 1. Forex: EURUSD on IDEALPRO
    steps.append(
        await _run_step(
            "fx_details_EURUSD",
            server.get_contract_details("EURUSD", sec_type="CASH", exchange="IDEALPRO"),
        )
    )
    steps.append(
        await _run_step(
            "fx_bars_EURUSD",
            server.get_historical_bars("EURUSD", sec_type="CASH", exchange="IDEALPRO", duration="1 D", bar_size="1 hour"),
        )
    )
    steps.append(
        await _run_step(
            "fx_quote_EURUSD",
            server.get_quote("EURUSD", sec_type="CASH", exchange="IDEALPRO", market_data_type=args.market_data_type),
        )
    )

    # 2. Future: front-month ES on CME
    es_details = await _run_step(
        "fut_details_ES",
        server.get_contract_details("ES", sec_type="FUT", exchange="CME"),
    )
    steps.append(es_details)
    es_expiry = _pick_front_expiry(es_details.get("data", {}).get("contracts", []))
    if es_expiry:
        steps.append(
            await _run_step(
                f"fut_bars_ES_{es_expiry}",
                server.get_historical_bars(
                    "ES", sec_type="FUT", exchange="CME", expiry=es_expiry, duration="1 D", bar_size="1 hour"
                ),
            )
        )
        steps.append(
            await _run_step(
                f"fut_quote_ES_{es_expiry}",
                server.get_quote(
                    "ES", sec_type="FUT", exchange="CME", expiry=es_expiry, market_data_type=args.market_data_type
                ),
            )
        )
    else:
        steps.append({"step": "fut_bars_ES", "status": "SKIP", "error": "No ES contract expiry returned"})

    # 3. Bond: US Treasury via con_id
    bond_con_id = args.bond_con_id
    if not bond_con_id:
        bond_details = await _run_step(
            f"bond_details_{args.bond_symbol}",
            server.get_contract_details(args.bond_symbol, sec_type="BOND", exchange="SMART"),
        )
        steps.append(bond_details)
        bond_contracts = bond_details.get("data", {}).get("contracts", [])
        if bond_contracts:
            bond_con_id = int(bond_contracts[0].get("conId") or 0)

    if bond_con_id:
        steps.append(
            await _run_step(
                f"bond_bars_{bond_con_id}",
                server.get_historical_bars(
                    "", sec_type="BOND", exchange="SMART", con_id=bond_con_id, duration="5 D", bar_size="1 day"
                ),
            )
        )
        steps.append(
            await _run_step(
                f"bond_quote_{bond_con_id}",
                server.get_quote(
                    "",
                    sec_type="BOND",
                    exchange="SMART",
                    con_id=bond_con_id,
                    market_data_type=args.market_data_type,
                ),
            )
        )
    else:
        steps.append(
            {
                "step": "bond_bars",
                "status": "SKIP",
                "error": "No bond contract returned; pass --bond-con-id <conId> if your IB account requires an explicit CUSIP/conId",
            }
        )

    # 4. Optional what-if preview (never submits an order)
    if args.preview:
        steps.append(
            await _run_step(
                "preview_whatif_EURUSD",
                server.preview_order(
                    "EURUSD",
                    "BUY",
                    20000,
                    sec_type="CASH",
                    exchange="IDEALPRO",
                    order_type="LMT",
                    limit_price=0.50,
                ),
            )
        )

    return report


def _print_report(report: dict[str, Any]) -> None:
    ping = report["ping"]
    print(f"IB target: {ping['host']}:{ping['port']} (clientId={ping['clientId']}, ordersEnabled={ping['ordersEnabled']})")
    print(f"TCP check: {'OK' if report['tcp']['ok'] else 'FAIL'} - {report['tcp']['detail']}")
    if not report["tcp"]["ok"]:
        print(report.get("hint", ""))
        return

    print("\nSmoke test results:")
    for item in report["steps"]:
        status = item["status"]
        detail = ""
        if item.get("error"):
            detail = f" - {item['error']}"
        elif item.get("entitlementMessages"):
            detail = f" - {'; '.join(item['entitlementMessages'])}"
        elif isinstance(item.get("data"), dict):
            d = item["data"]
            if "bars" in d:
                detail = f" - {len(d['bars'])} bars ({d.get('what_to_show')})"
            elif "contracts" in d:
                detail = f" - {d.get('count', len(d['contracts']))} contracts"
            elif "positions" in d:
                detail = f" - {len(d['positions'])} positions"
            elif "summary" in d:
                detail = f" - {len(d['summary'])} account(s)"
            elif "bid" in d or "ask" in d:
                detail = f" - bid={d.get('bid')} ask={d.get('ask')} last={d.get('last')} close={d.get('close')}"
            elif "initMarginChange" in d:
                detail = f" - sent={d.get('sent')} initMarginChange={d.get('initMarginChange')} commission={d.get('commission')}"
        print(f"  [{status:<18}] {item['step']}{detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only IB bridge smoke test against local TWS/Gateway.")
    parser.add_argument("--host", default="", help="Override IB_HOST (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="Override IB_PORT (7497 TWS paper, 4002 Gateway paper)")
    parser.add_argument("--client-id", type=int, default=None, help="Override IB_CLIENT_ID (default 77)")
    parser.add_argument("--account", default="", help="Override IB_ACCOUNT")
    parser.add_argument(
        "--market-data-type",
        type=int,
        default=3,
        choices=[1, 2, 3, 4],
        help="IB market data type for quotes: 1=live, 2=frozen, 3=delayed fallback (default), 4=delayed-frozen",
    )
    parser.add_argument("--bond-symbol", default="US-T", help="IB symbol for Treasury bond lookup (default US-T)")
    parser.add_argument("--bond-con-id", type=int, default=0, help="Explicit IB conId for bond bars/quote")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Also run an IB what-if preview_order on EURUSD (sends nothing; requires Read-Only API off in TWS/Gateway)",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args(argv)

    report = asyncio.run(run_smoke(args))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    if not report["tcp"]["ok"]:
        return 2
    if any(s["status"] == "FAIL" for s in report["steps"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
