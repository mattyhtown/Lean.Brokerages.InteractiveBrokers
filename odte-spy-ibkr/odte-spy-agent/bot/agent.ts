import { defineAgent } from "@grok/agent-sdk";
import { EGRESS_DOMAINS, SECRET_NAMES } from "./lib/hosting.js";

export default defineAgent({
  model: {
    id: "grok-4.7",
    params: [
      { id: "reasoning_effort", value: "high" },
      { id: "fast", value: "true" },
    ],
  },
  // No built-in tools: no shell and no file edits, so the model can reach the
  // engine only through bot/tools/, where the safety flags are fixed.
  tools: [],
  hosting: {
    egressDomains: [...EGRESS_DOMAINS],
    secretNames: [...SECRET_NAMES],
  },
});
