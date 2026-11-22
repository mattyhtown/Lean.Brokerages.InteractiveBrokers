import http from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import {
  CBOE_BAD_ETF_HOLIDAY_DATES,
  CboeRawOption,
  analyzeCboeChain,
  padCik,
  parseFredCsv,
  recentMondaysUtc,
  requireSecUserAgent,
  roundTo,
  sanitizeSocrataSearch,
  stripXsltPrefix,
  toFiniteNumber,
} from "./parsers.js";

const KRAKEN_BASE = "https://api.kraken.com/0/public";
const CBOE_QUOTES_BASE = "https://cdn.cboe.com/api/global/delayed_quotes";
const CBOE_DAILY_STATS_BASE = "https://cdn.cboe.com/data/us/options/market_statistics/daily";
const FRED_CSV_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv";
const TREASURY_DIRECT_BASE = "https://www.treasurydirect.gov/TA_WS/securities";
const FINRA_OTC_BASE = "https://api.finra.org/data/group/otcMarket/name";
const CFTC_LEGACY_COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json";
const FNG_URL = "https://api.alternative.me/fng/";
const SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json";
const SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions";
const SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data";

const FETCH_TIMEOUT_MS = 20_000;
const MAX_HTTP_BODY_BYTES = 1_048_576;

const CBOE_HEADERS = {
  "User-Agent": "Mozilla/5.0",
  Referer: "https://www.cboe.com/",
  Origin: "https://www.cboe.com",
  Accept: "application/json",
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const SYMBOL_RE = /^[A-Za-z0-9._^-]{1,16}$/;
const FRED_ID_RE = /^[A-Za-z0-9_.-]{1,64}$/;

async function fetchText(url: string, init?: RequestInit): Promise<{ status: number; text: string }> {
  const res = await fetch(url, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(FETCH_TIMEOUT_MS),
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${url}: ${text.slice(0, 400)}`);
  }
  return { status: res.status, text };
}

async function fetchJson<T = unknown>(url: string, init?: RequestInit): Promise<T> {
  const { text } = await fetchText(url, init);
  return JSON.parse(text) as T;
}

async function fetchKraken<T = Record<string, unknown>>(
  endpoint: string,
  params: URLSearchParams
): Promise<T> {
  const qs = params.toString();
  const url = `${KRAKEN_BASE}/${endpoint}${qs ? `?${qs}` : ""}`;
  const payload = await fetchJson<{ error?: string[]; result?: T }>(url);
  if (Array.isArray(payload.error) && payload.error.length > 0) {
    throw new Error(`Kraken API error: ${payload.error.join("; ")}`);
  }
  if (payload.result === undefined) {
    throw new Error(`Kraken API returned empty result for ${endpoint}`);
  }
  return payload.result;
}

function validateSymbol(raw: string): string {
  const sym = raw.trim().toUpperCase();
  if (!SYMBOL_RE.test(sym)) {
    throw new Error(`Invalid symbol: ${raw}`);
  }
  return sym;
}

function asTextResult(payload: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
  };
}

export function createMarketDataServer(): McpServer {
  const server = new McpServer({
    name: "market-data",
    version: "1.0.0",
  });

  // --- 1. Kraken Public Market Data (keyless, live) ---

  server.tool(
    "get_kraken_ticker",
    "Get live Kraken spot ticker (bid, ask, last, 24h volume, VWAP, high, low) for a pair such as XBTUSD, ETHUSD, SOLUSD.",
    {
      pair: z.string().min(2).max(32).describe("Kraken pair code, e.g. XBTUSD, ETHUSD, SOLUSD"),
    },
    async ({ pair }) => {
      const cleanPair = pair.trim().toUpperCase();
      const result = await fetchKraken<Record<string, Record<string, unknown>>>(
        "Ticker",
        new URLSearchParams({ pair: cleanPair })
      );
      const entries = Object.entries(result).map(([key, t]) => {
        const ask = Array.isArray(t.a) ? t.a : [];
        const bid = Array.isArray(t.b) ? t.b : [];
        const last = Array.isArray(t.c) ? t.c : [];
        const vol = Array.isArray(t.v) ? t.v : [];
        const vwap = Array.isArray(t.p) ? t.p : [];
        const trades = Array.isArray(t.t) ? t.t : [];
        const low = Array.isArray(t.l) ? t.l : [];
        const high = Array.isArray(t.h) ? t.h : [];
        return {
          pair: key,
          ask: toFiniteNumber(ask[0]),
          askLotVolume: toFiniteNumber(ask[2]),
          bid: toFiniteNumber(bid[0]),
          bidLotVolume: toFiniteNumber(bid[2]),
          last: toFiniteNumber(last[0]),
          lastVolume: toFiniteNumber(last[1]),
          volumeToday: toFiniteNumber(vol[0]),
          volume24h: toFiniteNumber(vol[1]),
          vwapToday: toFiniteNumber(vwap[0]),
          vwap24h: toFiniteNumber(vwap[1]),
          tradeCount24h: toFiniteNumber(trades[1]),
          low24h: toFiniteNumber(low[1]),
          high24h: toFiniteNumber(high[1]),
          openToday: toFiniteNumber(t.o),
        };
      });
      return asTextResult({ source: "kraken", tickers: entries });
    }
  );

  server.tool(
    "get_kraken_orderbook",
    "Get live Kraken L2 order book depth (bids and asks) for a crypto pair.",
    {
      pair: z.string().min(2).max(32).describe("Kraken pair code, e.g. XBTUSD, ETHUSD"),
      count: z.number().int().min(1).max(100).default(15).describe("Levels per side (default 15, max 100)"),
    },
    async ({ pair, count }) => {
      const cleanPair = pair.trim().toUpperCase();
      const result = await fetchKraken<
        Record<string, { asks?: [string, string, number][]; bids?: [string, string, number][] }>
      >("Depth", new URLSearchParams({ pair: cleanPair, count: String(count) }));
      const [bookPair, book] = Object.entries(result)[0] ?? [cleanPair, {}];
      const mapLevels = (levels?: [string, string, number][]) =>
        (levels ?? []).map(([price, volume, timestamp]) => ({
          price: toFiniteNumber(price),
          volume: toFiniteNumber(volume),
          timestamp,
        }));
      return asTextResult({
        source: "kraken",
        pair: bookPair,
        asks: mapLevels(book.asks),
        bids: mapLevels(book.bids),
      });
    }
  );

  server.tool(
    "get_kraken_ohlc",
    "Get live Kraken OHLCV candles for a crypto pair.",
    {
      pair: z.string().min(2).max(32).describe("Kraken pair code, e.g. XBTUSD"),
      interval: z
        .enum(["1", "5", "15", "30", "60", "240", "1440", "10080", "21600"])
        .default("60")
        .describe("Candle interval in minutes (default 60)"),
      limit: z.number().int().min(1).max(720).default(50).describe("Trailing candles to return (default 50)"),
      since: z.number().int().positive().optional().describe("Optional UNIX timestamp cursor"),
    },
    async ({ pair, interval, limit, since }) => {
      const params = new URLSearchParams({
        pair: pair.trim().toUpperCase(),
        interval,
      });
      if (since !== undefined) params.set("since", String(since));
      const result = await fetchKraken<Record<string, unknown>>("OHLC", params);
      const last = result.last;
      const pairEntry = Object.entries(result).find(([k]) => k !== "last");
      const rawCandles = Array.isArray(pairEntry?.[1]) ? (pairEntry[1] as unknown[][]) : [];
      const candles = rawCandles.slice(-limit).map((row) => {
        const ts = Number(row[0]);
        return {
          time: ts,
          isoTime: Number.isFinite(ts) ? new Date(ts * 1000).toISOString() : null,
          open: toFiniteNumber(row[1]),
          high: toFiniteNumber(row[2]),
          low: toFiniteNumber(row[3]),
          close: toFiniteNumber(row[4]),
          vwap: toFiniteNumber(row[5]),
          volume: toFiniteNumber(row[6]),
          count: toFiniteNumber(row[7]),
        };
      });
      return asTextResult({
        source: "kraken",
        pair: pairEntry?.[0] ?? pair.trim().toUpperCase(),
        intervalMinutes: Number(interval),
        last,
        count: candles.length,
        candles,
      });
    }
  );

  server.tool(
    "get_kraken_trades",
    "Get recent live Kraken trade prints for a crypto pair.",
    {
      pair: z.string().min(2).max(32).describe("Kraken pair code, e.g. XBTUSD"),
      count: z.number().int().min(1).max(200).default(25).describe("Number of recent trades (default 25, max 200)"),
    },
    async ({ pair, count }) => {
      const result = await fetchKraken<Record<string, unknown>>(
        "Trades",
        new URLSearchParams({ pair: pair.trim().toUpperCase(), count: String(count) })
      );
      const last = result.last;
      const pairEntry = Object.entries(result).find(([k]) => k !== "last");
      const rawTrades = Array.isArray(pairEntry?.[1]) ? (pairEntry[1] as unknown[][]) : [];
      const trades = rawTrades.slice(-count).map((row) => {
        const ts = Number(row[2]);
        return {
          price: toFiniteNumber(row[0]),
          volume: toFiniteNumber(row[1]),
          time: ts,
          isoTime: Number.isFinite(ts) ? new Date(ts * 1000).toISOString() : null,
          side: row[3] === "b" ? "buy" : row[3] === "s" ? "sell" : String(row[3] ?? ""),
          orderType: row[4] === "m" ? "market" : row[4] === "l" ? "limit" : String(row[4] ?? ""),
        };
      });
      return asTextResult({
        source: "kraken",
        pair: pairEntry?.[0] ?? pair.trim().toUpperCase(),
        last,
        count: trades.length,
        trades,
      });
    }
  );

  server.tool(
    "list_kraken_pairs",
    "Search live tradable Kraken spot asset pairs (e.g. query='BTC', 'ETH', 'USD').",
    {
      query: z.string().optional().describe("Case-insensitive filter matched against pair, altname, or wsname"),
      limit: z.number().int().min(1).max(200).default(50).describe("Max pairs to return (default 50)"),
    },
    async ({ query, limit }) => {
      const result = await fetchKraken<Record<string, Record<string, unknown>>>(
        "AssetPairs",
        new URLSearchParams()
      );
      const q = query?.trim().toLowerCase() ?? "";
      const pairs = Object.entries(result)
        .filter(([key, info]) => {
          if (!q) return true;
          const hay = `${key} ${String(info.altname ?? "")} ${String(info.wsname ?? "")}`.toLowerCase();
          return hay.includes(q);
        })
        .slice(0, limit)
        .map(([key, info]) => ({
          pair: key,
          altname: info.altname ?? null,
          wsname: info.wsname ?? null,
          base: info.base ?? null,
          quote: info.quote ?? null,
          status: info.status ?? null,
          ordermin: info.ordermin ?? null,
        }));
      return asTextResult({ source: "kraken", count: pairs.length, pairs });
    }
  );

  // --- 2. CBOE Delayed Quotes, Options Analytics, Put/Call, History (keyless, live) ---

  server.tool(
    "get_cboe_quote",
    "Get live 15-min delayed CBOE quote including IV30, bid, ask, last, close, and volume. Use leading underscore for indices (_SPX, _VIX, _OVX, _RUT).",
    {
      symbol: z.string().describe("Ticker symbol (e.g. NVDA, SPY, BRK.B) or index with underscore (_SPX, _VIX, _OVX)"),
    },
    async ({ symbol }) => {
      const sym = validateSymbol(symbol);
      const url = `${CBOE_QUOTES_BASE}/quotes/${encodeURIComponent(sym)}.json`;
      const payload = await fetchJson<{ timestamp?: string; data?: Record<string, unknown> }>(url, {
        headers: CBOE_HEADERS,
      });
      const d = payload.data ?? {};
      return asTextResult({
        source: "cboe_delayed",
        timestamp: payload.timestamp ?? null,
        symbol: d.symbol ?? sym,
        securityType: d.security_type ?? null,
        currentPrice: toFiniteNumber(d.current_price),
        bid: toFiniteNumber(d.bid),
        ask: toFiniteNumber(d.ask),
        open: toFiniteNumber(d.open),
        high: toFiniteNumber(d.high),
        low: toFiniteNumber(d.low),
        close: toFiniteNumber(d.close),
        prevDayClose: toFiniteNumber(d.prev_day_close),
        priceChange: toFiniteNumber(d.price_change),
        priceChangePercent: toFiniteNumber(d.price_change_percent),
        volume: toFiniteNumber(d.volume),
        iv30: toFiniteNumber(d.iv30),
        iv30Change: toFiniteNumber(d.iv30_change),
        iv30ChangePercent: toFiniteNumber(d.iv30_change_percent),
      });
    }
  );

  server.tool(
    "get_cboe_options_analytics",
    "Get live CBOE option-chain analytics for a stock, ETF, or index (_SPX): IV30, call/put volume & OI ratios, ATM straddle implied move, and ATM greeks slice.",
    {
      symbol: z.string().describe("Ticker (e.g. NVDA, SPY) or index with underscore (_SPX, _VIX)"),
      expiration: z
        .string()
        .regex(DATE_RE, "expiration must be YYYY-MM-DD")
        .optional()
        .describe("Optional expiration date YYYY-MM-DD (defaults to nearest upcoming)"),
      strikes_around_atm: z
        .number()
        .int()
        .min(1)
        .max(15)
        .default(5)
        .describe("Number of strikes above and below ATM to return (default 5)"),
    },
    async ({ symbol, expiration, strikes_around_atm }) => {
      const sym = validateSymbol(symbol);
      const url = `${CBOE_QUOTES_BASE}/options/${encodeURIComponent(sym)}.json`;
      const payload = await fetchJson<{
        timestamp?: string;
        data?: {
          current_price?: number;
          close?: number;
          iv30?: number;
          options?: CboeRawOption[];
        };
      }>(url, { headers: CBOE_HEADERS });
      const d = payload.data ?? {};
      const spot = toFiniteNumber(d.current_price) ?? toFiniteNumber(d.close);
      const iv30 = toFiniteNumber(d.iv30);
      const analysis = analyzeCboeChain(sym, spot, iv30, d.options ?? [], {
        expiration,
        strikesAroundAtm: strikes_around_atm,
      });
      return asTextResult({
        source: "cboe_delayed_options",
        timestamp: payload.timestamp ?? null,
        ...analysis,
      });
    }
  );

  server.tool(
    "get_cboe_market_put_call",
    "Get whole-market CBOE daily put/call ratios (total, equity, index, ETP, SPX, VIX) and call/put volumes.",
    {
      date: z
        .string()
        .regex(DATE_RE, "date must be YYYY-MM-DD")
        .optional()
        .describe("Trading date YYYY-MM-DD (omit to auto-find the latest available trading day)"),
    },
    async ({ date }) => {
      if (date) {
        const url = `${CBOE_DAILY_STATS_BASE}/${date}_daily_options`;
        const data = await fetchJson(url, { headers: CBOE_HEADERS });
        return asTextResult({ source: "cboe_daily_stats", date, data });
      }
      const now = new Date();
      for (let offset = 0; offset < 10; offset++) {
        const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - offset));
        const dow = d.getUTCDay();
        if (dow === 0 || dow === 6) continue;
        const iso = d.toISOString().slice(0, 10);
        const url = `${CBOE_DAILY_STATS_BASE}/${iso}_daily_options`;
        const res = await fetch(url, {
          headers: CBOE_HEADERS,
          signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        });
        if (res.ok) {
          const data = await res.json();
          return asTextResult({ source: "cboe_daily_stats", date: iso, data });
        }
      }
      throw new Error("Could not find a recent CBOE daily market statistics file in the last 10 days.");
    }
  );

  server.tool(
    "get_cboe_history",
    "Get daily historical OHLCV bars from CBOE (indices like _SPX back to 1975, ETFs/stocks back to 2004).",
    {
      symbol: z.string().describe("Ticker (e.g. SPY, NVDA) or index (_SPX, _VIX)"),
      limit: z.number().int().min(1).max(2000).default(60).describe("Trailing sessions to return (default 60)"),
      include_unfiltered: z
        .boolean()
        .default(false)
        .describe("If false (default), removes known corrupted CBOE holiday rows on non-index symbols"),
    },
    async ({ symbol, limit, include_unfiltered }) => {
      const sym = validateSymbol(symbol);
      const url = `${CBOE_QUOTES_BASE}/charts/historical/${encodeURIComponent(sym)}.json`;
      const payload = await fetchJson<{ data?: Array<Record<string, unknown>> }>(url, {
        headers: CBOE_HEADERS,
      });
      let bars = payload.data ?? [];
      if (!include_unfiltered && !sym.startsWith("_")) {
        bars = bars.filter((row) => !CBOE_BAD_ETF_HOLIDAY_DATES.has(String(row.date ?? "")));
      }
      const sliced = bars.slice(-limit);
      return asTextResult({
        source: "cboe_historical",
        symbol: sym,
        totalAvailable: bars.length,
        count: sliced.length,
        bars: sliced,
      });
    }
  );

  // --- 3. FRED Macro, Yields, Commodities, Volatility (keyless CSV, live) ---

  server.tool(
    "get_fred_series",
    "Fetch live FRED macro, Treasury yield, inflation, labor, volatility, or commodity time series without an API key (e.g. DGS10, DGS2, T10Y2Y, DFF, SOFR, CPIAUCSL, UNRATE, VIXCLS, DCOILWTICO, BAMLH0A0HYM2).",
    {
      series_id: z
        .string()
        .regex(FRED_ID_RE, "Invalid FRED series ID")
        .describe("FRED series ID, e.g. DGS10, DFF, CPIAUCSL, UNRATE, VIXCLS, DCOILWTICO"),
      start_date: z.string().regex(DATE_RE, "start_date must be YYYY-MM-DD").optional(),
      end_date: z.string().regex(DATE_RE, "end_date must be YYYY-MM-DD").optional(),
      limit: z.number().int().min(1).max(2000).default(60).describe("Trailing observations to return (default 60)"),
    },
    async ({ series_id, start_date, end_date, limit }) => {
      if (start_date && end_date && start_date > end_date) {
        throw new Error("start_date must be on or before end_date");
      }
      const id = series_id.trim().toUpperCase();
      const params = new URLSearchParams({ id });
      if (start_date) params.set("cosd", start_date);
      if (end_date) params.set("coed", end_date);
      const { text } = await fetchText(`${FRED_CSV_BASE}?${params.toString()}`, {
        headers: { Accept: "text/csv" },
      });
      const observations = parseFredCsv(text);
      const latest = observations.length > 0 ? observations[observations.length - 1] : null;
      const previous = observations.length > 1 ? observations[observations.length - 2] : null;
      const change =
        latest && previous ? roundTo(latest.value - previous.value, 6) : null;
      const sliced = observations.slice(-limit);
      return asTextResult({
        source: "fred_public_csv",
        seriesId: id,
        totalObservations: observations.length,
        latest,
        previous,
        change,
        count: sliced.length,
        observations: sliced,
      });
    }
  );

  // --- 4. US Treasury Auctions (TreasuryDirect, keyless, live) ---

  server.tool(
    "get_treasury_auctions",
    "Get live US Treasury auction schedule or recent auction results (high yield, bid-to-cover, indirect/direct/dealer awards) from TreasuryDirect.",
    {
      mode: z
        .enum(["upcoming", "recent", "by_type"])
        .default("recent")
        .describe("upcoming = announced schedule, recent = auctioned in last N days, by_type = history for one security type"),
      security_type: z
        .enum(["Note", "Bond", "TIPS", "FRN", "Bill"])
        .optional()
        .describe("Required when mode='by_type'"),
      days: z.number().int().min(1).max(90).default(14).describe("Lookback days when mode='recent' (default 14)"),
      limit: z.number().int().min(1).max(100).default(20).describe("Max auctions to return (default 20)"),
    },
    async ({ mode, security_type, days, limit }) => {
      let url: string;
      if (mode === "upcoming") {
        url = `${TREASURY_DIRECT_BASE}/upcoming?format=json`;
      } else if (mode === "recent") {
        url = `${TREASURY_DIRECT_BASE}/auctioned?format=json&days=${days}`;
      } else {
        if (!security_type) {
          throw new Error("security_type (Note, Bond, TIPS, FRN, or Bill) is required when mode='by_type'");
        }
        url = `${TREASURY_DIRECT_BASE}/auctioned?format=json&type=${encodeURIComponent(security_type)}&pagesize=${limit}`;
      }
      const rows = await fetchJson<Array<Record<string, unknown>>>(url);
      const compact = rows.slice(0, limit).map((r) => ({
        cusip: r.cusip ?? null,
        securityType: r.securityType ?? null,
        securityTerm: r.securityTerm ?? null,
        originalSecurityTerm: r.originalSecurityTerm ?? null,
        reopening: r.reopening ?? null,
        announcementDate: r.announcementDate ?? null,
        issueDate: r.issueDate ?? null,
        maturityDate: r.maturityDate ?? null,
        auctionDate: r.competitiveAuctionCloseDate ?? r.recordDate ?? null,
        offeringAmount: r.offeringAmount ?? null,
        interestRate: r.interestRate ?? null,
        highYield: r.highYield ?? null,
        medianYield: r.medianYield ?? null,
        lowYield: r.lowYield ?? null,
        highRate: r.highRate ?? null,
        investmentRate: r.investmentRate ?? null,
        bidToCoverRatio: r.bidToCoverRatio ?? null,
        indirectBiddersAccepted: r.indirectBiddersCompetitiveParAwarded ?? null,
        directBiddersAccepted: r.directBiddersCompetitiveParAwarded ?? null,
        primaryDealersAccepted: r.primaryDealersCompetitiveParAwarded ?? null,
        updatedTimestamp: r.updatedTimestamp ?? null,
      }));
      return asTextResult({
        source: "treasury_direct",
        mode,
        count: compact.length,
        auctions: compact,
      });
    }
  );

  // --- 5. FINRA Consolidated Short Interest & Weekly ATS Dark Pools (keyless, live) ---

  server.tool(
    "get_finra_short_interest",
    "Get live FINRA semi-monthly consolidated short interest (shares short, prior period, % change, avg daily volume, days to cover) for a US equity symbol.",
    {
      symbol: z.string().describe("US stock/ETF ticker, e.g. NVDA, GME, TSLA"),
      start_date: z
        .string()
        .regex(DATE_RE, "start_date must be YYYY-MM-DD")
        .optional()
        .describe("Optional settlement start date (defaults to 180 days ago)"),
      limit: z.number().int().min(1).max(50).default(12).describe("Max settlement periods to return (default 12)"),
    },
    async ({ symbol, start_date, limit }) => {
      const sym = validateSymbol(symbol);
      const now = new Date();
      const defaultStart = new Date(now.getTime() - 180 * 86_400_000).toISOString().slice(0, 10);
      const endDate = new Date(now.getTime() + 7 * 86_400_000).toISOString().slice(0, 10);
      const res = await fetch(`${FINRA_OTC_BASE}/consolidatedShortInterest`, {
        method: "POST",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          compareFilters: [{ compareType: "EQUAL", fieldName: "symbolCode", fieldValue: sym }],
          dateRangeFilters: [
            { fieldName: "settlementDate", startDate: start_date ?? defaultStart, endDate },
          ],
          limit: 500,
        }),
      });
      if (res.status === 204) {
        return asTextResult({ source: "finra_short_interest", symbol: sym, count: 0, records: [] });
      }
      if (!res.ok) {
        throw new Error(`FINRA short interest HTTP ${res.status}: ${(await res.text()).slice(0, 400)}`);
      }
      const rows = (await res.json()) as Array<Record<string, unknown>>;
      rows.sort((a, b) => String(b.settlementDate ?? "").localeCompare(String(a.settlementDate ?? "")));
      const records = rows.slice(0, limit).map((r) => ({
        settlementDate: r.settlementDate ?? null,
        symbol: r.symbolCode ?? sym,
        issueName: r.issueName ?? null,
        sharesShort: toFiniteNumber(r.currentShortPositionQuantity),
        previousSharesShort: toFiniteNumber(r.previousShortPositionQuantity),
        changeShares: toFiniteNumber(r.changePreviousNumber),
        changePercent: toFiniteNumber(r.changePercent),
        averageDailyVolume: toFiniteNumber(r.averageDailyVolumeQuantity),
        daysToCover: toFiniteNumber(r.daysToCoverQuantity),
      }));
      return asTextResult({
        source: "finra_short_interest",
        symbol: sym,
        count: records.length,
        records,
      });
    }
  );

  server.tool(
    "get_finra_dark_pool_ats",
    "Get live FINRA weekly dark-pool (ATS) share & trade volume by venue for a US equity symbol. Auto-finds the latest published Monday if week_start_date is omitted.",
    {
      symbol: z.string().describe("US stock/ETF ticker, e.g. NVDA, AAPL, SPY"),
      week_start_date: z
        .string()
        .regex(DATE_RE, "week_start_date must be a Monday in YYYY-MM-DD")
        .optional()
        .describe("Optional Monday YYYY-MM-DD (omit to auto-find latest published week)"),
      limit: z.number().int().min(1).max(100).default(25).describe("Max ATS venues to return (default 25)"),
    },
    async ({ symbol, week_start_date, limit }) => {
      const sym = validateSymbol(symbol);
      const weeksToTry = week_start_date ? [week_start_date] : recentMondaysUtc(8);
      for (const week of weeksToTry) {
        const res = await fetch(`${FINRA_OTC_BASE}/weeklySummary`, {
          method: "POST",
          signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({
            compareFilters: [
              { compareType: "EQUAL", fieldName: "weekStartDate", fieldValue: week },
              { compareType: "EQUAL", fieldName: "issueSymbolIdentifier", fieldValue: sym },
              { compareType: "EQUAL", fieldName: "summaryTypeCode", fieldValue: "ATS_W_SMBL_FIRM" },
            ],
            limit: 100,
          }),
        });
        if (res.status === 204) {
          continue;
        }
        if (!res.ok) {
          throw new Error(`FINRA ATS HTTP ${res.status}: ${(await res.text()).slice(0, 400)}`);
        }
        const rows = (await res.json()) as Array<Record<string, unknown>>;
        if (rows.length === 0) {
          continue;
        }
        rows.sort(
          (a, b) =>
            (toFiniteNumber(b.totalWeeklyShareQuantity) ?? 0) -
            (toFiniteNumber(a.totalWeeklyShareQuantity) ?? 0)
        );
        const totalShares = rows.reduce((acc, r) => acc + (toFiniteNumber(r.totalWeeklyShareQuantity) ?? 0), 0);
        const totalTrades = rows.reduce((acc, r) => acc + (toFiniteNumber(r.totalWeeklyTradeCount) ?? 0), 0);
        const venues = rows.slice(0, limit).map((r) => ({
          mpid: r.MPID ?? null,
          venueName: r.marketParticipantName ?? null,
          shares: toFiniteNumber(r.totalWeeklyShareQuantity),
          trades: toFiniteNumber(r.totalWeeklyTradeCount),
          notionalUsd: toFiniteNumber(r.totalNotionalSum),
          lastReportedDate: r.lastReportedDate ?? null,
        }));
        return asTextResult({
          source: "finra_ats_weekly",
          symbol: sym,
          weekStartDate: week,
          venueCount: rows.length,
          totalAtsShares: totalShares,
          totalAtsTrades: totalTrades,
          venues,
        });
      }
      return asTextResult({
        source: "finra_ats_weekly",
        symbol: sym,
        weekStartDate: week_start_date ?? null,
        venueCount: 0,
        totalAtsShares: 0,
        totalAtsTrades: 0,
        venues: [],
      });
    }
  );

  // --- 6. CFTC Commitments of Traders (COT) Futures Positioning (keyless, live) ---

  server.tool(
    "get_cftc_cot",
    "Get live CFTC weekly Legacy Futures-Only Commitments of Traders (COT) positioning (open interest, speculative & commercial long/short/net).",
    {
      market_search: z
        .string()
        .min(2)
        .max(80)
        .describe("Case-insensitive CFTC market name substring, e.g. 'E-MINI S&P 500', 'CRUDE OIL', 'GOLD', 'EURO FX', 'BITCOIN'"),
      limit: z.number().int().min(1).max(52).default(10).describe("Number of weekly rows to return (default 10)"),
    },
    async ({ market_search, limit }) => {
      const cleaned = sanitizeSocrataSearch(market_search);
      if (!cleaned) {
        throw new Error("market_search must contain searchable characters");
      }
      const params = new URLSearchParams({
        $where: `upper(market_and_exchange_names) like '%${cleaned}%'`,
        $order: "report_date_as_yyyy_mm_dd DESC",
        $limit: String(limit),
      });
      const rows = await fetchJson<Array<Record<string, unknown>>>(
        `${CFTC_LEGACY_COT_URL}?${params.toString()}`
      );
      const reports = rows.map((r) => {
        const ncLong = toFiniteNumber(r.noncomm_positions_long_all);
        const ncShort = toFiniteNumber(r.noncomm_positions_short_all);
        const cLong = toFiniteNumber(r.comm_positions_long_all);
        const cShort = toFiniteNumber(r.comm_positions_short_all);
        return {
          reportDate: String(r.report_date_as_yyyy_mm_dd ?? "").slice(0, 10),
          market: r.market_and_exchange_names ?? null,
          contractCode: r.cftc_contract_market_code ?? null,
          openInterest: toFiniteNumber(r.open_interest_all),
          nonCommercialLong: ncLong,
          nonCommercialShort: ncShort,
          nonCommercialNet: ncLong !== null && ncShort !== null ? ncLong - ncShort : null,
          commercialLong: cLong,
          commercialShort: cShort,
          commercialNet: cLong !== null && cShort !== null ? cLong - cShort : null,
          changeInOpenInterest: toFiniteNumber(r.change_in_oi_all),
        };
      });
      return asTextResult({
        source: "cftc_legacy_cot",
        query: market_search,
        count: reports.length,
        reports,
      });
    }
  );

  // --- 7. Crypto Fear & Greed Sentiment (keyless, live) ---

  server.tool(
    "get_crypto_fear_greed",
    "Get live Crypto Fear & Greed Index history from alternative.me (0=Extreme Fear to 100=Extreme Greed).",
    {
      limit: z.number().int().min(1).max(90).default(7).describe("Number of daily readings (default 7)"),
    },
    async ({ limit }) => {
      const payload = await fetchJson<{
        data?: Array<{ value?: string; value_classification?: string; timestamp?: string }>;
      }>(`${FNG_URL}?limit=${limit}`);
      const readings = (payload.data ?? []).map((item) => {
        const ts = toFiniteNumber(item.timestamp);
        return {
          value: toFiniteNumber(item.value),
          classification: item.value_classification ?? null,
          timestamp: ts,
          isoDate: ts !== null ? new Date(ts * 1000).toISOString().slice(0, 10) : null,
        };
      });
      return asTextResult({ source: "alternative_me_fng", count: readings.length, readings });
    }
  );

  // --- 8. SEC EDGAR Recent Filings Index (requires SEC_USER_AGENT env var) ---

  server.tool(
    "get_sec_filings",
    "Fetch live SEC EDGAR recent filings index (10-K, 10-Q, 8-K, Form 4, 13F-HR) with raw archive URLs. Requires SEC_USER_AGENT='Name email@domain' in server env.",
    {
      ticker: z.string().optional().describe("Issuer ticker symbol (e.g. AAPL, NVDA)"),
      cik: z.string().optional().describe("CIK (1-10 digits; required for 13F managers without a ticker)"),
      form: z.string().optional().describe("Optional form filter, e.g. '10-K', '10-Q', '8-K', '4', '13F-HR'"),
      limit: z.number().int().min(1).max(50).default(15).describe("Max filings to return (default 15)"),
    },
    async ({ ticker, cik, form, limit }) => {
      if (!ticker && !cik) {
        throw new Error("Provide either ticker or cik");
      }
      const userAgent = requireSecUserAgent(process.env.SEC_USER_AGENT);
      const secHeaders = {
        "User-Agent": userAgent,
        Accept: "application/json",
      };
      let cik10: string;
      if (cik) {
        cik10 = padCik(cik);
      } else {
        const sym = validateSymbol(ticker!);
        const map = await fetchJson<Record<string, { cik_str?: number | string; ticker?: string }>>(
          SEC_TICKERS_URL,
          { headers: secHeaders }
        );
        const found = Object.values(map).find(
          (entry) => String(entry.ticker ?? "").toUpperCase() === sym
        );
        if (!found || found.cik_str === undefined) {
          throw new Error(`Ticker ${sym} not found in SEC company_tickers.json`);
        }
        cik10 = padCik(found.cik_str);
      }

      const subs = await fetchJson<{
        name?: string;
        tickers?: string[];
        filings?: {
          recent?: {
            form?: string[];
            accessionNumber?: string[];
            filingDate?: string[];
            reportDate?: string[];
            primaryDocument?: string[];
          };
        };
      }>(`${SEC_SUBMISSIONS_BASE}/CIK${cik10}.json`, { headers: secHeaders });

      const recent = subs.filings?.recent ?? {};
      const forms = recent.form ?? [];
      const accs = recent.accessionNumber ?? [];
      const fdates = recent.filingDate ?? [];
      const rdates = recent.reportDate ?? [];
      const pdocs = recent.primaryDocument ?? [];
      const cikInt = String(Number(cik10));
      const wantedForm = form?.trim().toUpperCase();

      const filings: Array<Record<string, string | null>> = [];
      for (let i = 0; i < forms.length && filings.length < limit; i++) {
        const f = forms[i] ?? "";
        if (wantedForm && f.toUpperCase() !== wantedForm) continue;
        const acc = accs[i] ?? "";
        const pdoc = stripXsltPrefix(pdocs[i] ?? "");
        const accNoDash = acc.replace(/-/g, "");
        filings.push({
          form: f,
          filingDate: fdates[i] ?? null,
          reportDate: rdates[i] || null,
          accessionNumber: acc,
          primaryDocument: pdoc || null,
          archiveUrl: accNoDash && pdoc ? `${SEC_ARCHIVES_BASE}/${cikInt}/${accNoDash}/${pdoc}` : null,
          indexUrl: accNoDash ? `${SEC_ARCHIVES_BASE}/${cikInt}/${accNoDash}/index.json` : null,
        });
      }

      return asTextResult({
        source: "sec_edgar",
        cik: cik10,
        entityName: subs.name ?? null,
        tickers: subs.tickers ?? [],
        count: filings.length,
        filings,
      });
    }
  );

  return server;
}

function readRequestBody(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let total = 0;
    req.on("data", (chunk: Buffer) => {
      total += chunk.length;
      if (total > MAX_HTTP_BODY_BYTES) {
        reject(new Error("Request body exceeds 1 MiB limit"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8").trim();
      if (!raw) {
        resolve(undefined);
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

async function runHttpServer(host: string, port: number): Promise<void> {
  const httpServer = http.createServer(async (req, res) => {
    const urlPath = (req.url ?? "/").split("?")[0];
    if (urlPath === "/health" && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          ok: true,
          server: "market-data",
          transport: "streamable-http",
          endpoint: "/mcp",
          secUserAgentConfigured: Boolean(process.env.SEC_USER_AGENT),
        })
      );
      return;
    }

    if (urlPath !== "/mcp") {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Not found. Use POST /mcp or GET /health." }));
      return;
    }

    if (req.method !== "POST") {
      res.writeHead(405, { "Content-Type": "application/json", Allow: "POST" });
      res.end(
        JSON.stringify({
          jsonrpc: "2.0",
          error: { code: -32000, message: "Method not allowed; stateless MCP accepts POST /mcp." },
          id: null,
        })
      );
      return;
    }

    let body: unknown;
    try {
      body = await readRequestBody(req);
    } catch (err) {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          jsonrpc: "2.0",
          error: { code: -32700, message: err instanceof Error ? err.message : "Invalid JSON body" },
          id: null,
        })
      );
      return;
    }

    const mcpServer = createMarketDataServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });
    res.on("close", () => {
      void transport.close();
      void mcpServer.close();
    });
    try {
      await mcpServer.connect(transport);
      await transport.handleRequest(req, res, body);
    } catch (err) {
      if (!res.headersSent) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            jsonrpc: "2.0",
            error: {
              code: -32603,
              message: err instanceof Error ? err.message : "Internal MCP server error",
            },
            id: null,
          })
        );
      }
    }
  });

  await new Promise<void>((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(port, host, () => resolve());
  });
  console.error(`market-data MCP HTTP server listening on http://${host}:${port}/mcp (health: /health)`);
}

function parseCliArgs(argv: string[]) {
  let httpMode = false;
  let host = process.env.MARKET_DATA_HOST || "127.0.0.1";
  let port = Number(process.env.MARKET_DATA_PORT || "3100");
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--http") {
      httpMode = true;
    } else if (arg === "--host") {
      const next = argv[++i];
      if (!next) throw new Error("--host requires a value");
      host = next;
    } else if (arg === "--port") {
      const next = argv[++i];
      if (!next) throw new Error("--port requires a value");
      port = Number(next);
    } else {
      throw new Error(`Unknown CLI option: ${arg}`);
    }
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid port: ${port}`);
  }
  return { httpMode, host, port };
}

async function main() {
  const { httpMode, host, port } = parseCliArgs(process.argv.slice(2));
  if (httpMode) {
    await runHttpServer(host, port);
    return;
  }
  const server = createMarketDataServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
