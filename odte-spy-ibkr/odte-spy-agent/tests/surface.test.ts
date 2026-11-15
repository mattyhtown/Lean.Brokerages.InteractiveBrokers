import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { loadProject } from "@grok/agent-sdk";
import agent from "../bot/agent.js";
import { SECRET_NAMES } from "../bot/lib/hosting.js";

const AGENT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("the agent exposes the five engine tools and nothing that trades", async () => {
  const { info } = await loadProject(AGENT_DIR, { strict: true });
  const names = info.tools.map((tool) => tool.name).sort();
  assert.deepEqual(names, [
    "backtest",
    "improvement_run",
    "research_evaluate",
    "research_ingest",
    "signal_state",
  ]);
  for (const name of names) {
    assert.doesNotMatch(name, /order|trade|place|promot|live/i);
  }
});

test("the model has no built-in tools, so no shell and no file edits", () => {
  assert.deepEqual(agent.tools, []);
});

test("the deployment declares every secret name and no CURSOR_ name", () => {
  assert.deepEqual(agent.hosting?.secretNames, [...SECRET_NAMES]);
  assert.deepEqual(
    [...SECRET_NAMES].sort(),
    [
      "ALPACA_API_KEY",
      "ALPACA_API_SECRET",
      "ORATS_API_TOKEN",
      "QUANTCONNECT_API_TOKEN",
      "QUANTCONNECT_USER_ID",
      "THETADATA_PASSWORD",
      "THETADATA_USERNAME",
      "TRADING_ECONOMICS_API_KEY",
      "UNUSUAL_WHALES_API_KEY",
    ],
  );
  for (const name of SECRET_NAMES) {
    assert.equal(name.startsWith("CURSOR_"), false);
  }
});
