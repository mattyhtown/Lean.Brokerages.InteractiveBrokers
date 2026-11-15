import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { researchIngest } from "../lib/operations.js";

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "use YYYY-MM-DD");

export default defineTool({
  description: prompt`
    Pull real market data into the research cache
    (python -m odte_spy_ibkr.research ingest). Sources: alpaca (SPY minute
    bars), uw (Unusual Whales flow and chains), orats (historical 0DTE chain).
    Each source needs its secret set on this deployment and spends that
    provider's API quota. The engine CLI currently runs alpaca on every
    ingest as well as the sources asked for, so every ingest needs the
    Alpaca keys; sourcesRun lists what actually ran. IBKR is not a source
    here: it needs a TWS or Gateway session that this agent cannot reach.
  `,
  inputSchema: z.object({
    sources: z.array(z.enum(["alpaca", "uw", "orats"])).min(1).default(["alpaca"]),
    start: isoDate.optional().describe("First session. Defaults to end minus days."),
    end: isoDate.optional().describe("Last session. Defaults to today."),
    days: z.number().int().min(1).max(400).optional().describe("Lookback when start is omitted."),
  }),
  effect: "write",
  async execute(input) {
    return researchIngest(input);
  },
});
