import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import { signalState } from "../lib/operations.js";

export default defineTool({
  description: prompt`
    Read the current signal and gate state: safety flags, which providers
    and credentials are present, spot and flow, the risk, macro, flow and
    Sharpe gates, and the active parameters. Fields that need an IB session
    (net call bias, option chain, entry intent) come back null with a reason.
  `,
  effect: "read",
  async execute() {
    return signalState();
  },
});
