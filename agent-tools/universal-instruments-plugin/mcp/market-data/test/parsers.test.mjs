import test from "node:test";
import assert from "node:assert/strict";
import {
  analyzeCboeChain,
  padCik,
  parseFredCsv,
  parseStandardOccSymbol,
  recentMondaysUtc,
  requireSecUserAgent,
  sanitizeSocrataSearch,
  stripXsltPrefix,
} from "../dist/parsers.js";

test("parseStandardOccSymbol parses standard contracts and skips adjusted roots with digits", () => {
  const std = parseStandardOccSymbol("NVDA261127C00130000");
  assert.deepEqual(std, {
    root: "NVDA",
    expiration: "2026-11-27",
    type: "call",
    strike: 130,
  });

  const put = parseStandardOccSymbol("SPXW261218P05850500");
  assert.deepEqual(put, {
    root: "SPXW",
    expiration: "2026-12-18",
    type: "put",
    strike: 5850.5,
  });

  assert.equal(parseStandardOccSymbol("B1VDA261127C00135000"), null);
});

test("analyzeCboeChain computes put/call ratios and ATM straddle implied move", () => {
  const chain = [
    // Adjusted contract must be excluded
    { option: "B1VDA261127C00130000", bid: 99, ask: 101, volume: 9999, open_interest: 9999 },
    // Expired contract should not be selected as nearest upcoming
    { option: "NVDA261113C00130000", bid: 1, ask: 2, volume: 10, open_interest: 20 },
    // Active nearest expiration
    { option: "NVDA261127C00130000", bid: 4.9, ask: 5.1, iv: 0.55, delta: 0.51, volume: 200, open_interest: 1000 },
    { option: "NVDA261127P00130000", bid: 4.7, ask: 4.9, iv: 0.56, delta: -0.49, volume: 150, open_interest: 800 },
    { option: "NVDA261127C00135000", bid: 2.0, ask: 2.2, iv: 0.52, delta: 0.32, volume: 100, open_interest: 500 },
  ];

  const out = analyzeCboeChain("NVDA", 130.0, 54.2, chain, { todayIso: "2026-11-21" });
  assert.equal(out.standardContractCount, 4);
  assert.equal(out.selectedExpiration, "2026-11-27");
  assert.equal(out.totals.callVolume, 310);
  assert.equal(out.totals.putVolume, 150);
  assert.equal(out.totals.putCallVolumeRatio, 0.4839);
  assert.deepEqual(out.atmStraddle, {
    expiration: "2026-11-27",
    strike: 130,
    callMid: 5,
    putMid: 4.8,
    straddlePrice: 9.8,
    impliedMovePct: 7.54,
  });
});

test("analyzeCboeChain keeps ATM call and put on the same OCC root", () => {
  const chain = [
    { option: "SPXW261218C05850000", bid: 40, ask: 42, volume: 50, open_interest: 100 },
    { option: "SPX261218C05850000", bid: 50, ask: 52, volume: 80, open_interest: 200 },
    { option: "SPXW261218P05850000", bid: 38, ask: 40, volume: 60, open_interest: 120 },
    { option: "SPX261218P05850000", bid: 48, ask: 50, volume: 90, open_interest: 210 },
  ];
  const out = analyzeCboeChain("_SPX", 5850, 15.0, chain, { todayIso: "2026-11-21" });
  assert.deepEqual(out.atmStraddle, {
    expiration: "2026-12-18",
    strike: 5850,
    callMid: 51,
    putMid: 49,
    straddlePrice: 100,
    impliedMovePct: 1.71,
  });
});

test("parseFredCsv skips missing dot rows and parses numbers", () => {
  const csv = "observation_date,DGS10\n2026-11-17,4.98\n2026-11-18,.\n2026-11-19,5.04\n";
  assert.deepEqual(parseFredCsv(csv), [
    { date: "2026-11-17", value: 4.98 },
    { date: "2026-11-19", value: 5.04 },
  ]);
});

test("recentMondaysUtc returns preceding Mondays in descending order", () => {
  const sat = new Date("2026-11-21T12:00:00Z");
  const mondays = recentMondaysUtc(3, sat);
  assert.deepEqual(mondays, ["2026-11-09", "2026-11-02", "2026-10-26"]);
});

test("sanitizeSocrataSearch escapes quotes and strips wildcards", () => {
  assert.equal(sanitizeSocrataSearch("e-mini s&p 500% 'test'"), "E-MINI S&P 500 ''TEST''");
});

test("SEC helpers enforce contact email, pad CIK, and strip XSLT prefix", () => {
  assert.throws(() => requireSecUserAgent(""), /SEC_USER_AGENT/);
  assert.throws(() => requireSecUserAgent("MyApp/1.0"), /SEC_USER_AGENT/);
  assert.equal(requireSecUserAgent("Jane Doe jane@example.com"), "Jane Doe jane@example.com");
  assert.equal(padCik(320193), "0000320193");
  assert.equal(stripXsltPrefix("xslF345X05/form4.xml"), "form4.xml");
});
