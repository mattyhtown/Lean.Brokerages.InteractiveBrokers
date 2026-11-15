import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const KALSHI_BASE =
  process.env.KALSHI_API_BASE ?? "https://api.elections.kalshi.com/trade-api/v2";
const POLYMARKET_GAMMA =
  process.env.POLYMARKET_GAMMA_BASE ?? "https://gamma-api.polymarket.com";
const POLYMARKET_CLOB =
  process.env.POLYMARKET_CLOB_BASE ?? "https://clob.polymarket.com";

async function fetchJson(url: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`HTTP ${res.status} ${url}: ${body.slice(0, 500)}`);
  }
  return res.json() as Promise<unknown>;
}

const server = new McpServer({
  name: "prediction-markets",
  version: "1.0.0",
});

server.tool(
  "list_kalshi_markets",
  "List Kalshi markets (read-only). Supports status filter and pagination cursor.",
  {
    status: z
      .enum(["open", "closed", "settled", "unopened"])
      .optional()
      .describe("Market status filter"),
    limit: z
      .number()
      .int()
      .min(1)
      .max(200)
      .optional()
      .describe("Page size (default 50)"),
    cursor: z.string().optional().describe("Pagination cursor from prior response"),
    series_ticker: z
      .string()
      .optional()
      .describe("Filter by series ticker (e.g. KXHIGHNY)"),
  },
  async ({ status, limit, cursor, series_ticker }) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (limit) params.set("limit", String(limit));
    if (cursor) params.set("cursor", cursor);
    if (series_ticker) params.set("series_ticker", series_ticker);
    const url = `${KALSHI_BASE}/markets?${params.toString()}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "get_kalshi_market",
  "Get one Kalshi market by ticker (read-only).",
  {
    ticker: z.string().describe("Market ticker"),
  },
  async ({ ticker }) => {
    const url = `${KALSHI_BASE}/markets/${encodeURIComponent(ticker)}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "get_kalshi_orderbook",
  "Get Kalshi order book for a market ticker (read-only).",
  {
    ticker: z.string().describe("Market ticker"),
    depth: z
      .number()
      .int()
      .min(1)
      .max(100)
      .optional()
      .describe("Depth levels (if supported by API)"),
  },
  async ({ ticker, depth }) => {
    const params = new URLSearchParams();
    if (depth) params.set("depth", String(depth));
    const qs = params.toString();
    const url = `${KALSHI_BASE}/markets/${encodeURIComponent(ticker)}/orderbook${qs ? `?${qs}` : ""}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "list_polymarket_markets",
  "List Polymarket markets from the Gamma API (read-only).",
  {
    limit: z.number().int().min(1).max(100).optional().describe("Max results (default 25)"),
    offset: z.number().int().min(0).optional().describe("Offset for pagination"),
    closed: z
      .boolean()
      .optional()
      .describe("If true, include closed markets; false for active only"),
    slug: z.string().optional().describe("Filter by market slug"),
  },
  async ({ limit, offset, closed, slug }) => {
    const params = new URLSearchParams();
    params.set("limit", String(limit ?? 25));
    if (offset !== undefined) params.set("offset", String(offset));
    if (closed !== undefined) params.set("closed", String(closed));
    if (slug) params.set("slug", slug);
    const url = `${POLYMARKET_GAMMA}/markets?${params.toString()}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "get_polymarket_market",
  "Get a Polymarket market by id or slug (read-only).",
  {
    id: z.string().optional().describe("Gamma market id"),
    slug: z.string().optional().describe("Market slug"),
  },
  async ({ id, slug }) => {
    if (!id && !slug) {
      throw new Error("Provide id or slug");
    }
    const path = id
      ? `/markets/${encodeURIComponent(id)}`
      : `/markets/slug/${encodeURIComponent(slug!)}`;
    const url = `${POLYMARKET_GAMMA}${path}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "get_polymarket_orderbook",
  "Get Polymarket CLOB order book for a token id (read-only).",
  {
    token_id: z.string().describe("CLOB token id (from market clobTokenIds)"),
  },
  async ({ token_id }) => {
    const url = `${POLYMARKET_CLOB}/book?token_id=${encodeURIComponent(token_id)}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "get_kalshi_trades",
  "List executed trades for a Kalshi market, newest first (read-only).",
  {
    ticker: z.string().describe("Market ticker"),
    limit: z
      .number()
      .int()
      .min(1)
      .max(1000)
      .optional()
      .describe("Page size (default 100)"),
    cursor: z.string().optional().describe("Pagination cursor from prior response"),
    min_ts: z
      .number()
      .int()
      .optional()
      .describe("Only trades at or after this Unix time in seconds"),
    max_ts: z
      .number()
      .int()
      .optional()
      .describe("Only trades at or before this Unix time in seconds"),
  },
  async ({ ticker, limit, cursor, min_ts, max_ts }) => {
    const params = new URLSearchParams();
    params.set("ticker", ticker);
    if (limit) params.set("limit", String(limit));
    if (cursor) params.set("cursor", cursor);
    if (min_ts !== undefined) params.set("min_ts", String(min_ts));
    if (max_ts !== undefined) params.set("max_ts", String(max_ts));
    const url = `${KALSHI_BASE}/markets/trades?${params.toString()}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "get_kalshi_candlesticks",
  "Get price history for a Kalshi market as candlesticks: yes bid, yes ask, traded price, volume and open interest per period (read-only).",
  {
    series_ticker: z
      .string()
      .describe("Series ticker the market belongs to (e.g. KXFEDCUT); see the market's event"),
    ticker: z.string().describe("Market ticker"),
    start_ts: z.number().int().describe("Start of the range, Unix time in seconds"),
    end_ts: z.number().int().describe("End of the range, Unix time in seconds"),
    period_interval: z
      .union([z.literal(1), z.literal(60), z.literal(1440)])
      .describe("Candle length in minutes: 1, 60 or 1440"),
  },
  async ({ series_ticker, ticker, start_ts, end_ts, period_interval }) => {
    const params = new URLSearchParams();
    params.set("start_ts", String(start_ts));
    params.set("end_ts", String(end_ts));
    params.set("period_interval", String(period_interval));
    const url = `${KALSHI_BASE}/series/${encodeURIComponent(series_ticker)}/markets/${encodeURIComponent(ticker)}/candlesticks?${params.toString()}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

server.tool(
  "get_polymarket_price_history",
  "Get price history for a Polymarket outcome token from the CLOB API (read-only). Pass either interval, or start_ts and end_ts.",
  {
    token_id: z.string().describe("CLOB token id (from market clobTokenIds)"),
    interval: z
      .enum(["1h", "6h", "1d", "1w", "1m"])
      .optional()
      .describe("Look-back window ending now; 1m is one month"),
    start_ts: z
      .number()
      .int()
      .optional()
      .describe("Start of the range, Unix time in seconds (use with end_ts)"),
    end_ts: z
      .number()
      .int()
      .optional()
      .describe("End of the range, Unix time in seconds (use with start_ts)"),
    fidelity: z
      .number()
      .int()
      .min(1)
      .optional()
      .describe("Minutes between points"),
  },
  async ({ token_id, interval, start_ts, end_ts, fidelity }) => {
    const hasRange = start_ts !== undefined && end_ts !== undefined;
    if (!interval && !hasRange) {
      throw new Error("Provide interval, or both start_ts and end_ts");
    }
    const params = new URLSearchParams();
    params.set("market", token_id);
    if (interval) params.set("interval", interval);
    if (start_ts !== undefined) params.set("startTs", String(start_ts));
    if (end_ts !== undefined) params.set("endTs", String(end_ts));
    if (fidelity) params.set("fidelity", String(fidelity));
    const url = `${POLYMARKET_CLOB}/prices-history?${params.toString()}`;
    const data = await fetchJson(url);
    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});