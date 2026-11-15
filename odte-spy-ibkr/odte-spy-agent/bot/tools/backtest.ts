import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { backtest } from "../lib/operations.js";

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "use YYYY-MM-DD");

export default defineTool({
  description: prompt`
    Run the historical backtest (python -m odte_spy_ibkr.backtest) and apply
    the rolling Sharpe check. Returns the summary, whether the check passed,
    and chainCoverage: where the price and option chain of each scored
    session came from, read before the run. With walkForward, only the
    holdout window is scored: sessions counts it, from and to bound it, and
    simulated_sessions is the whole range. Each scored session is in one
    count. no_local_spot_sessions had no price on disk: the engine skips
    such a session and scores it as a flat day, unless Alpaca returns a
    daily bar for it (no_local_spot_handling), and summary.skipped_no_spot
    is how many it skipped over the whole range. Only research_chain_sessions
    were simulated on that day's own chain, from the research cache. A
    cached chain is whatever an earlier backtest stored under that date. A
    session with no chain gets no_chain_substitute: Alpaca's current
    snapshot, which is today's chain and not that day's, or a synthetic
    chain. Neither is market evidence. chainCoverage is null when it could
    not be read. The range may span at most 400 days.
  `,
  inputSchema: z.object({
    from: isoDate,
    to: isoDate,
    requireRollingSharpe: z.number().min(0).max(10).default(1.5),
    walkForward: z.boolean().default(false).describe("Score only the holdout test window."),
    verbose: z.boolean().default(false).describe("Include per-session no-entry reasons."),
  }),
  // It caches the chains, bars and headlines it fetches in the engine folder.
  effect: "write",
  async execute(input) {
    return backtest(input);
  },
});
