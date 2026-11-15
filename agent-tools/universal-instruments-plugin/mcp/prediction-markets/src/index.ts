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

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});