import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import { signalState } from "../lib/operations.js";

export default defineTool({
  description: prompt`
    Read the current signal and gate state: safety flags, which providers
    and credentials are present, spot and flow, the risk, macro, flow and
    Sharpe gates, and the active parameters. Fields that need an IB session
    (net call bias, option chain, entry intent) come back null with a reason.
    A macro field is null when no provider had anything to read for it, and
    macro_unavailable_reasons says what was missing. With Alpaca keys the
    first call of a day fetches that day's headlines and caches them; later
    calls that day read the cache and see no newer headline.
  `,
  // The engine's macro_news provider caches the day's headlines once.
  effect: "idempotent",
  async execute() {
    return signalState();
  },
});
