"""Unit tests for SEC EDGAR Form 4 and 13F-HR XML parsing (no live network)."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import edgar_parse

SAMPLE_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2026-11-10</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Apple Inc.</issuerName>
    <issuerTradingSymbol>AAPL</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001214156</rptOwnerCik>
      <rptOwnerName>COOK TIMOTHY D</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-11-10</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>228.45</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>3200000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <securityTitle><value>Restricted Stock Unit</value></securityTitle>
      <transactionDate><value>2026-11-09</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>100000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>
"""

SAMPLE_13F_DEFAULT_NS = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>69900000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>300000000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <votingAuthority>
      <Sole>300000000</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
</informationTable>
"""

SAMPLE_13F_PREFIXED_NS = """<?xml version="1.0" encoding="UTF-8"?>
<ns1:informationTable xmlns:ns1="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <ns1:infoTable>
    <ns1:nameOfIssuer>SPDR S&amp;P 500 ETF TR</ns1:nameOfIssuer>
    <ns1:titleOfClass>TR UNIT</ns1:titleOfClass>
    <ns1:cusip>78462F103</ns1:cusip>
    <ns1:value>12500000</ns1:value>
    <ns1:shrsOrPrnAmt>
      <ns1:sshPrnamt>20000</ns1:sshPrnamt>
      <ns1:sshPrnamtType>SH</ns1:sshPrnamtType>
    </ns1:shrsOrPrnAmt>
    <ns1:putCall>Put</ns1:putCall>
    <ns1:investmentDiscretion>SOLE</ns1:investmentDiscretion>
    <ns1:votingAuthority>
      <ns1:Sole>20000</ns1:Sole>
      <ns1:Shared>0</ns1:Shared>
      <ns1:None>0</ns1:None>
    </ns1:votingAuthority>
  </ns1:infoTable>
</ns1:informationTable>
"""


class EdgarParseTest(unittest.TestCase):
    def test_require_user_agent_validation(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SEC_USER_AGENT"):
                edgar_parse.require_user_agent()
        for bad in ("", "MyApp/1.0", "user@localhost"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                edgar_parse.require_user_agent(bad)
        self.assertEqual(
            edgar_parse.require_user_agent("Jane Doe jane@example.com"),
            "Jane Doe jane@example.com",
        )

    def test_pad_cik_and_strip_xslt_prefix(self) -> None:
        self.assertEqual(edgar_parse.pad_cik(320193), "0000320193")
        self.assertEqual(edgar_parse.pad_cik("CIK0001067983"), "0001067983")
        self.assertEqual(
            edgar_parse.strip_xslt_prefix("xslF345X05/wk-form4_123.xml"),
            "wk-form4_123.xml",
        )
        self.assertEqual(edgar_parse.strip_xslt_prefix("form4.xml"), "form4.xml")

    def test_parse_form4_xml(self) -> None:
        parsed = edgar_parse.parse_form4_xml(SAMPLE_FORM4_XML)
        self.assertEqual(parsed["issuer"]["tradingSymbol"], "AAPL")
        self.assertEqual(len(parsed["reportingOwners"]), 1)
        owner = parsed["reportingOwners"][0]
        self.assertEqual(owner["name"], "COOK TIMOTHY D")
        self.assertTrue(owner["isOfficer"])
        self.assertEqual(owner["officerTitle"], "Chief Executive Officer")
        self.assertEqual(len(parsed["transactions"]), 2)

        sale = parsed["transactions"][0]
        self.assertEqual(sale["table"], "nonDerivative")
        self.assertEqual(sale["transactionCode"], "S")
        self.assertEqual(sale["acquiredDisposed"], "D")
        self.assertEqual(sale["shares"], 50000.0)
        self.assertEqual(sale["pricePerShare"], 228.45)
        self.assertEqual(sale["sharesOwnedFollowing"], 3200000.0)

        deriv = parsed["transactions"][1]
        self.assertEqual(deriv["table"], "derivative")
        self.assertEqual(deriv["transactionCode"], "M")

    def test_parse_13f_default_and_prefixed_namespaces(self) -> None:
        h1 = edgar_parse.parse_13f_infotable_xml(SAMPLE_13F_DEFAULT_NS)
        self.assertEqual(len(h1), 1)
        self.assertEqual(h1[0]["issuer"], "APPLE INC")
        self.assertEqual(h1[0]["cusip"], "037833100")
        self.assertEqual(h1[0]["value"], 69900000000)
        self.assertEqual(h1[0]["sharesOrPrincipal"], 300000000)
        self.assertIsNone(h1[0]["putCall"])
        self.assertEqual(h1[0]["votingAuthority"]["sole"], 300000000)

        h2 = edgar_parse.parse_13f_infotable_xml(SAMPLE_13F_PREFIXED_NS)
        self.assertEqual(len(h2), 1)
        self.assertEqual(h2[0]["issuer"], "SPDR S&P 500 ETF TR")
        self.assertEqual(h2[0]["putCall"], "Put")
        self.assertEqual(h2[0]["votingAuthority"]["sole"], 20000)

    def test_find_13f_infotable_filename(self) -> None:
        index_json = {
            "directory": {
                "item": [
                    {"name": "primary_doc.xml"},
                    {"name": "FilingSummary.xml"},
                    {"name": "R1.xml"},
                    {"name": "form13fInfoTable.xml"},
                ]
            }
        }
        self.assertEqual(
            edgar_parse.find_13f_infotable_filename(index_json, "xslForm13F_X02/primary_doc.xml"),
            "form13fInfoTable.xml",
        )

    def test_select_13f_prefers_newest_report_date_over_late_old_amendment(self) -> None:
        recent = {
            "form": ["13F-HR/A", "13F-HR", "13F-HR/A"],
            "accessionNumber": [
                "0001067983-26-000015",
                "0001067983-26-000012",
                "0001067983-26-000014",
            ],
            "filingDate": ["2026-11-18", "2026-11-14", "2026-11-16"],
            "reportDate": ["2025-12-31", "2026-09-30", "2026-09-30"],
            "primaryDocument": ["primary_doc.xml", "primary_doc.xml", "primary_doc.xml"],
        }
        candidates = edgar_parse.select_13f_candidates(recent)
        self.assertEqual(
            [c["accessionNumber"] for c in candidates],
            [
                "0001067983-26-000014",  # newest reportDate (2026-09-30) + newest filingDate (2026-11-16)
                "0001067983-26-000012",  # newest reportDate (2026-09-30) original filing
                "0001067983-26-000015",  # late amendment for older quarter (2025-12-31)
            ],
        )

    def test_fetch_latest_13f_skips_older_amendment_and_empty_amendment(self) -> None:
        subs_payload = {
            "name": "Example Capital LP",
            "filings": {
                "recent": {
                    "form": ["13F-HR/A", "13F-HR/A", "13F-HR"],
                    "accessionNumber": [
                        "0001067983-26-000099",  # late amendment for older Q4 2025
                        "0001067983-26-000050",  # cover-only amendment for Q3 2026 (no info table)
                        "0001067983-26-000040",  # full Q3 2026 13F-HR
                    ],
                    "filingDate": ["2026-11-20", "2026-11-16", "2026-11-14"],
                    "reportDate": ["2025-12-31", "2026-09-30", "2026-09-30"],
                    "primaryDocument": ["primary_doc.xml", "primary_doc.xml", "primary_doc.xml"],
                }
            },
        }
        responses = {
            "https://data.sec.gov/submissions/CIK0001067983.json": json.dumps(subs_payload).encode(),
            "https://www.sec.gov/Archives/edgar/data/1067983/000106798326000050/index.json": json.dumps(
                {"directory": {"item": [{"name": "primary_doc.xml"}]}}
            ).encode(),
            "https://www.sec.gov/Archives/edgar/data/1067983/000106798326000040/index.json": json.dumps(
                {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "form13fInfoTable.xml"}]}}
            ).encode(),
            "https://www.sec.gov/Archives/edgar/data/1067983/000106798326000040/form13fInfoTable.xml": SAMPLE_13F_DEFAULT_NS.encode(),
        }

        def fake_get(url: str, user_agent: str, timeout: float = 20.0) -> bytes:
            if url not in responses:
                raise AssertionError(f"Unexpected URL fetched: {url}")
            return responses[url]

        with mock.patch.object(edgar_parse, "_sec_get", side_effect=fake_get):
            result = edgar_parse.fetch_latest_13f("1067983", top=10, user_agent="Jane Doe jane@example.com")

        self.assertEqual(result["accessionNumber"], "0001067983-26-000040")
        self.assertEqual(result["reportDate"], "2026-09-30")
        self.assertEqual(result["form"], "13F-HR")
        self.assertEqual(result["totalPositions"], 1)


if __name__ == "__main__":
    unittest.main()
