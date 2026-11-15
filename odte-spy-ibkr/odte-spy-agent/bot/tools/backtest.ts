import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { backtest } from "../lib/operations.js";

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "use YYYY-MM-DD");

export default defineTool({
  description: prompt`
    Run the historical backtest (python -m odte_spy_ibkr.backtest) and apply
    the rolling Sharpe check. Returns the summary and whether the check
    passed. A day with no cached option chain uses a synthetic chain when
    alpacaKeysPresent is false, so that result is not market evidence.
  `,
  inputSchema: z.object({
    from: isoDate,
    to: isoDate,
    requireRollingSharpe: z.number().min(0).max(10).default(1.5),
    walkForward: z.boolean().default(false).describe("Score only the holdout test window."),
    verbose: z.boolean().default(false).describe("Include per-session no-entry reasons."),
  }),
  effect: "read",
  async execute(input) {
    return backtest(input);
  },
});
