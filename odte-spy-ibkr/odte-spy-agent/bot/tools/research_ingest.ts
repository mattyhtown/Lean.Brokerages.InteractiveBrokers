import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { researchIngest } from "../lib/operations.js";

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "use YYYY-MM-DD");

export default defineTool({
  description: prompt`
    Pull real data into the research cache
    (python -m odte_spy_ibkr.research ingest). Sources: alpaca (SPY minute
    bars), uw (Unusual Whales flow and chains), orats (historical 0DTE
    chain), tradingeconomics (US economic calendar), quantconnect.
    Each source needs its secret set on this deployment and spends that
    provider's API quota. quantconnect here only checks the login and
    imports LEAN files already on disk: the paid download is switched off,
    so on a fresh disk it imports nothing. A start-to-end range may span
    at most 400 days. A failure with cacheMayHaveChanged true can leave
    the files of the sources that finished before it in the cache.
    Not sources here: ibkr needs a TWS or Gateway session and thetadata
    needs a Theta Terminal on the same machine. This agent has neither.
  `,
  inputSchema: z.object({
    sources: z.array(z.enum(["alpaca", "uw", "orats", "tradingeconomics", "quantconnect"])).min(1).default(["alpaca"]),
    start: isoDate.optional().describe("First session. Defaults to end minus days."),
    end: isoDate.optional().describe("Last session. Defaults to today."),
    days: z.number().int().min(1).max(400).optional().describe("Lookback when start is omitted."),
  }),
  effect: "write",
  async execute(input) {
    return researchIngest(input);
  },
});
