import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { researchEvaluate } from "../lib/operations.js";

export default defineTool({
  description: prompt`
    Run the walk-forward feature evaluation with a final holdout
    (python -m odte_spy_ibkr.research evaluate) on the research cache and
    return its Markdown report. Run 2 (the default) uses the holdout the
    engine fixed in advance; run 1 uses the last holdoutDays sessions, a
    holdout the research log already counts as spent. Every evaluation
    looks at a holdout, so do not repeat it to hunt for a better result.
    Fails when the cache has too few real minute-bar sessions.
  `,
  inputSchema: z.object({
    run: z.union([z.literal(1), z.literal(2)]).default(2),
    holdoutDays: z
      .number()
      .int()
      .min(1)
      .max(120)
      .optional()
      .describe("Run 1 only. Defaults to 8."),
  }),
  effect: "read",
  async execute(input) {
    return researchEvaluate(input);
  },
});
