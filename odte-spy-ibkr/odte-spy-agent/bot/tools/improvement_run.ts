import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import { improvementRun } from "../lib/operations.js";

export default defineTool({
  description: prompt`
    Run one improvement pass (python -m odte_spy_ibkr.improvement run):
    score challenger parameter sets on the walk-forward holdout and write a
    history record. This agent never promotes: params/active.yaml stays as
    it is. decision is the engine's promotion rule applied to that record,
    and qualifiedForPaperPromotion says whether the best challenger cleared
    the Sharpe target and margin (null when the decision could not be read).
  `,
  effect: "write",
  async execute() {
    return improvementRun();
  },
});
