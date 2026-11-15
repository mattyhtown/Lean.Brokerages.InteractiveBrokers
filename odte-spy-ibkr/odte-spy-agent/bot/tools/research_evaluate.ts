import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { researchEvaluate } from "../lib/operations.js";

export default defineTool({
  description: prompt`
    Run the walk-forward feature evaluation with a final holdout
    (python -m odte_spy_ibkr.research evaluate) on the research cache and
    return its Markdown report. Fails when the cache has too few real
    minute-bar sessions.
  `,
  inputSchema: z.object({
    holdoutDays: z.number().int().min(1).max(120).default(20),
  }),
  effect: "read",
  async execute(input) {
    return researchEvaluate(input);
  },
});
