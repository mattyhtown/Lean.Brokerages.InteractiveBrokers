#!/usr/bin/env python3
"""Parse SEC EDGAR Form 4 (insider trades) and 13F-HR (institutional holdings) XML.

Uses the Python standard library only. Requires SEC_USER_AGENT in the environment in the
form "Name email@domain" per SEC EDGAR fair-access rules.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

USER_AGENT_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
TICKER_RE = re.compile(r"^[A-Za-z0-9._-]{1,12}$")
XSLT_PREFIX_RE = re.compile(r"^xsl[^/]*/", re.IGNORECASE)
REQUEST_INTERVAL_SECONDS = 0.12
_LAST_REQUEST_TS = 0.0


def require_user_agent(user_agent: str | None = None) -> str:
    ua = (user_agent if user_agent is not None else os.environ.get("SEC_USER_AGENT", "")).strip()
    if not ua or not USER_AGENT_EMAIL_RE.search(ua):
        raise ValueError(
            "SEC_USER_AGENT must be set to a real contact in the form 'Name email@domain' "
            "(for example: export SEC_USER_AGENT='Jane Doe jane@example.com'). "
            "SEC EDGAR returns HTTP 403 without a declared contact."
        )
    return ua


def pad_cik(cik: str | int) -> str:
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError(f"Invalid CIK: {cik!r}")
    return digits.zfill(10)


def strip_xslt_prefix(document_name: str) -> str:
    return XSLT_PREFIX_RE.sub("", document_name.strip().lstrip("/"))


def _sec_get(url: str, user_agent: str, timeout: float = 20.0) -> bytes:
    global _LAST_REQUEST_TS
    wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - _LAST_REQUEST_TS)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _LAST_REQUEST_TS = time.monotonic()
            return resp.read()
    except urllib.error.HTTPError as exc:
        _LAST_REQUEST_TS = time.monotonic()
        raise RuntimeError(f"SEC EDGAR returned HTTP {exc.code} for {url}") from exc


def resolve_cik(ticker: str, user_agent: str) -> str:
    sym = ticker.strip().upper()
    if not TICKER_RE.match(sym):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    data = json.loads(_sec_get(SEC_TICKERS_URL, user_agent).decode("utf-8"))
    for entry in data.values():
        if str(entry.get("ticker", "")).upper() == sym:
            return pad_cik(entry["cik_str"])
    raise LookupError(f"Ticker {sym!r} not found in SEC company_tickers.json")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element | None, *path: str) -> str | None:
    cur = elem
    for name in path:
        if cur is None:
            return None
        nxt = None
        for child in cur:
            if _local(child.tag) == name:
                nxt = child
                break
        cur = nxt
    if cur is None or cur.text is None:
        return None
    val = cur.text.strip()
    return val if val != "" else None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except ValueError:
        return None


def _to_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "y", "yes")


def parse_form4_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)

    issuer = {
        "cik": _child_text(root, "issuer", "issuerCik"),
        "name": _child_text(root, "issuer", "issuerName"),
        "tradingSymbol": _child_text(root, "issuer", "issuerTradingSymbol"),
    }

    owners: list[dict[str, Any]] = []
    for child in root:
        if _local(child.tag) != "reportingOwner":
            continue
        owners.append(
            {
                "cik": _child_text(child, "reportingOwnerId", "rptOwnerCik"),
                "name": _child_text(child, "reportingOwnerId", "rptOwnerName"),
                "isDirector": _to_bool(_child_text(child, "reportingOwnerRelationship", "isDirector")),
                "isOfficer": _to_bool(_child_text(child, "reportingOwnerRelationship", "isOfficer")),
                "isTenPercentOwner": _to_bool(
                    _child_text(child, "reportingOwnerRelationship", "isTenPercentOwner")
                ),
                "officerTitle": _child_text(child, "reportingOwnerRelationship", "officerTitle"),
            }
        )

    transactions: list[dict[str, Any]] = []
    table_specs = [
        ("nonDerivativeTable", "nonDerivativeTransaction", "nonDerivative"),
        ("derivativeTable", "derivativeTransaction", "derivative"),
    ]
    for table_tag, tx_tag, table_label in table_specs:
        for table_el in root:
            if _local(table_el.tag) != table_tag:
                continue
            for tx in table_el:
                if _local(tx.tag) != tx_tag:
                    continue
                transactions.append(
                    {
                        "table": table_label,
                        "securityTitle": _child_text(tx, "securityTitle", "value"),
                        "transactionDate": _child_text(tx, "transactionDate", "value"),
                        "transactionCode": _child_text(tx, "transactionCoding", "transactionCode"),
                        "acquiredDisposed": _child_text(
                            tx, "transactionAmounts", "transactionAcquiredDisposedCode", "value"
                        ),
                        "shares": _to_float(_child_text(tx, "transactionAmounts", "transactionShares", "value")),
                        "pricePerShare": _to_float(
                            _child_text(tx, "transactionAmounts", "transactionPricePerShare", "value")
                        ),
                        "sharesOwnedFollowing": _to_float(
                            _child_text(
                                tx, "postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"
                            )
                        ),
                        "directOrIndirect": _child_text(tx, "ownershipNature", "directOrIndirectOwnership", "value"),
                    }
                )

    return {
        "documentType": _child_text(root, "documentType"),
        "periodOfReport": _child_text(root, "periodOfReport"),
        "issuer": issuer,
        "reportingOwners": owners,
        "transactions": transactions,
    }


def parse_13f_infotable_xml(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    holdings: list[dict[str, Any]] = []
    for elem in root.iter():
        if _local(elem.tag) != "infoTable":
            continue
        holdings.append(
            {
                "issuer": _child_text(elem, "nameOfIssuer"),
                "titleOfClass": _child_text(elem, "titleOfClass"),
                "cusip": _child_text(elem, "cusip"),
                "value": _to_int(_child_text(elem, "value")),
                "sharesOrPrincipal": _to_int(_child_text(elem, "shrsOrPrnAmt", "sshPrnamt")),
                "sharesOrPrincipalType": _child_text(elem, "shrsOrPrnAmt", "sshPrnamtType"),
                "putCall": _child_text(elem, "putCall"),
                "investmentDiscretion": _child_text(elem, "investmentDiscretion"),
                "votingAuthority": {
                    "sole": _to_int(_child_text(elem, "votingAuthority", "Sole")),
                    "shared": _to_int(_child_text(elem, "votingAuthority", "Shared")),
                    "none": _to_int(_child_text(elem, "votingAuthority", "None")),
                },
            }
        )
    return holdings


def find_13f_infotable_filename(index_json: dict[str, Any], primary_doc: str) -> str | None:
    items = index_json.get("directory", {}).get("item", [])
    primary_clean = strip_xslt_prefix(primary_doc).lower()
    candidates: list[str] = []
    for item in items:
        name = str(item.get("name", ""))
        lower = name.lower()
        if not lower.endswith(".xml"):
            continue
        if lower == primary_clean or lower == "filingsummary.xml" or re.match(r"^r\d+\.xml$", lower):
            continue
        candidates.append(name)

    for name in candidates:
        lower = name.lower()
        if "infotable" in lower or "informationtable" in lower or "table" in lower:
            return name
    return candidates[0] if candidates else None


def fetch_recent_form4(cik: str, limit: int, user_agent: str) -> dict[str, Any]:
    cik10 = pad_cik(cik)
    cik_int = str(int(cik10))
    subs = json.loads(_sec_get(SEC_SUBMISSIONS_URL.format(cik=cik10), user_agent).decode("utf-8"))
    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    filings: list[dict[str, Any]] = []
    for form, acc, fdate, pdoc in zip(forms, accessions, filing_dates, primary_docs):
        if form not in ("4", "4/A"):
            continue
        raw_doc = strip_xslt_prefix(pdoc)
        if not raw_doc.lower().endswith(".xml"):
            continue
        acc_nodash = acc.replace("-", "")
        url = f"{SEC_ARCHIVES_BASE}/{cik_int}/{acc_nodash}/{raw_doc}"
        xml_text = _sec_get(url, user_agent).decode("utf-8", errors="replace")
        parsed = parse_form4_xml(xml_text)
        parsed["form"] = form
        parsed["filingDate"] = fdate
        parsed["accessionNumber"] = acc
        parsed["sourceUrl"] = url
        filings.append(parsed)
        if len(filings) >= limit:
            break

    return {
        "cik": cik10,
        "entityName": subs.get("name"),
        "count": len(filings),
        "filings": filings,
    }


def select_13f_candidates(recent: dict[str, Any]) -> list[dict[str, str]]:
    """Return recent 13F-HR / 13F-HR/A filings ordered by newest reportDate, then filingDate."""
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    scored: list[tuple[tuple[str, str, int], dict[str, str]]] = []
    for idx, form in enumerate(forms):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        acc = accessions[idx] if idx < len(accessions) else ""
        if not acc:
            continue
        fdate = str((filing_dates[idx] if idx < len(filing_dates) else "") or "")
        rdate = str((report_dates[idx] if idx < len(report_dates) else "") or "")
        pdoc = str((primary_docs[idx] if idx < len(primary_docs) else "") or "")
        # Prefer newest report period so a late amendment for an older quarter does not
        # overshadow the current quarter's 13F-HR; tie-break by newest filingDate then EDGAR order.
        scored.append(
            (
                (rdate, fdate, -idx),
                {
                    "form": form,
                    "accessionNumber": acc,
                    "filingDate": fdate,
                    "reportDate": rdate,
                    "primaryDocument": pdoc,
                },
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored]


def fetch_latest_13f(cik: str, top: int, user_agent: str) -> dict[str, Any]:
    cik10 = pad_cik(cik)
    cik_int = str(int(cik10))
    subs = json.loads(_sec_get(SEC_SUBMISSIONS_URL.format(cik=cik10), user_agent).decode("utf-8"))
    recent = subs.get("filings", {}).get("recent", {})
    candidates = select_13f_candidates(recent)
    if not candidates:
        raise LookupError(f"No recent 13F-HR filing found for manager CIK {cik10}")

    last_index_url = ""
    for entry in candidates:
        acc = entry["accessionNumber"]
        acc_nodash = acc.replace("-", "")
        index_url = f"{SEC_ARCHIVES_BASE}/{cik_int}/{acc_nodash}/index.json"
        last_index_url = index_url
        index_json = json.loads(_sec_get(index_url, user_agent).decode("utf-8"))
        info_file = find_13f_infotable_filename(index_json, entry["primaryDocument"])
        if not info_file:
            continue
        table_url = f"{SEC_ARCHIVES_BASE}/{cik_int}/{acc_nodash}/{info_file}"
        xml_text = _sec_get(table_url, user_agent).decode("utf-8", errors="replace")
        holdings = parse_13f_infotable_xml(xml_text)
        holdings.sort(key=lambda h: h.get("value") or 0, reverse=True)
        return {
            "cik": cik10,
            "managerName": subs.get("name"),
            "form": entry["form"],
            "filingDate": entry["filingDate"],
            "reportDate": entry["reportDate"],
            "accessionNumber": acc,
            "sourceUrl": table_url,
            "valueNote": "Raw XML <value> field (SEC switched 13F reporting from $thousands to exact $USD starting Jan 2023).",
            "totalPositions": len(holdings),
            "holdings": holdings[:top] if top > 0 else holdings,
        }

    raise LookupError(f"Could not locate 13F information table XML in {last_index_url}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse SEC EDGAR Form 4 and 13F-HR XML filings.")
    sub = parser.add_subparsers(dest="command", required=True)

    p4 = sub.add_parser("form4", help="Fetch and parse recent Form 4 insider trade XML filings")
    p4_id = p4.add_mutually_exclusive_group(required=True)
    p4_id.add_argument("--ticker", help="Issuer ticker symbol (resolved via SEC company_tickers.json)")
    p4_id.add_argument("--cik", help="Issuer CIK (1-10 digits)")
    p4.add_argument("--limit", type=int, default=5, help="Max Form 4 filings to fetch (default 5)")

    p13 = sub.add_parser("13f", help="Fetch and parse the latest 13F-HR holdings XML for a manager CIK")
    p13.add_argument("--cik", required=True, help="Investment manager CIK (1-10 digits)")
    p13.add_argument("--top", type=int, default=25, help="Return top N holdings by value (0 for all; default 25)")

    args = parser.parse_args(argv)
    try:
        ua = require_user_agent()
        if args.command == "form4":
            if args.limit < 1:
                raise ValueError("--limit must be at least 1")
            cik = resolve_cik(args.ticker, ua) if args.ticker else pad_cik(args.cik)
            result = fetch_recent_form4(cik, args.limit, ua)
        else:
            if args.top < 0:
                raise ValueError("--top must be >= 0")
            result = fetch_latest_13f(args.cik, args.top, ua)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
