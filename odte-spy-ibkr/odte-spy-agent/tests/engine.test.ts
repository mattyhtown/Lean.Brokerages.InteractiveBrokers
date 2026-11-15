import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildEngineEnv,
  createEngineRunner,
  ENGINE_MODULES,
  redactSecrets,
  SAFETY_YAML_PATH,
  type EngineModule,
} from "../bot/lib/engine.js";

test("safety flags are fixed whatever the host environment says", () => {
  const env = buildEngineEnv({
    ENABLE_ORDERS: "true",
    LIVE_TRADING: "true",
    READ_ONLY: "false",
    PAPER: "false",
    IMPROVEMENT_AUTO_PROMOTE_LIVE: "true",
    IMPROVEMENT_AUTO_PROMOTE_PAPER: "true",
    QUANTCONNECT_ALLOW_PAID_DOWNLOAD: "true",
    CONFIG_YAML: "/tmp/attacker.yaml",
    ODTE_NO_DOTENV: "0",
  });
  assert.equal(env.ENABLE_ORDERS, "false");
  assert.equal(env.LIVE_TRADING, "false");
  assert.equal(env.READ_ONLY, "true");
  assert.equal(env.PAPER, "true");
  assert.equal(env.IMPROVEMENT_AUTO_PROMOTE_LIVE, "false");
  assert.equal(env.IMPROVEMENT_AUTO_PROMOTE_PAPER, "false");
  assert.equal(env.QUANTCONNECT_ALLOW_PAID_DOWNLOAD, "false");
  assert.equal(env.CONFIG_YAML, SAFETY_YAML_PATH);
  // The engine's own .env would bring in settings this allowlist left out.
  assert.equal(env.ODTE_NO_DOTENV, "1");
});

test("the engine environment is an allowlist", () => {
  const env = buildEngineEnv({
    PATH: "/usr/bin",
    ALPACA_API_KEY: "alpaca-key",
    QUANTCONNECT_API_TOKEN: "qc-token",
    IB_HOST: "10.0.0.5",
    IB_PORT: "7496",
    ALPHA_SCORED_ENTRY_ENABLED: "true",
    CURSOR_API_KEY: "cursor-key",
  });
  assert.equal(env.PATH, "/usr/bin");
  assert.equal(env.ALPACA_API_KEY, "alpaca-key");
  assert.equal(env.QUANTCONNECT_API_TOKEN, "qc-token");
  for (const name of ["IB_HOST", "IB_PORT", "ALPHA_SCORED_ENTRY_ENABLED", "CURSOR_API_KEY"]) {
    assert.equal(name in env, false, `${name} must not reach the engine`);
  }
});

test("the live runner is not a module the agent can start", async () => {
  assert.equal((ENGINE_MODULES as readonly string[]).includes("odte_spy_ibkr"), false);
  assert.equal((ENGINE_MODULES as readonly string[]).includes("odte_spy_ibkr.runner"), false);

  const run = await createEngineRunner({ ODTE_PYTHON: "/nonexistent/python" })({
    module: "odte_spy_ibkr.runner" as EngineModule,
    args: ["--once"],
    timeoutMs: 1000,
  });
  assert.equal(run.exitCode, null);
  assert.match(run.spawnError ?? "", /module not allowed/);
});

test("a missing Python interpreter is reported, not thrown", async () => {
  const run = await createEngineRunner({ ODTE_PYTHON: "/nonexistent/python" })({
    module: "odte_spy_ibkr.hosted_state",
    args: [],
    timeoutMs: 1000,
  });
  assert.equal(run.exitCode, null);
  assert.notEqual(run.spawnError, null);
});

test("secret values are redacted from engine output, raw and URL-encoded", () => {
  const env = { ORATS_API_TOKEN: "tok/en+123", ALPACA_API_SECRET: "s3cr3tvalue" };
  const text =
    "GET https://api.orats.io/datav2/x?token=tok%2Fen%2B123 failed; header s3cr3tvalue; raw tok/en+123";
  const out = redactSecrets(text, env);
  assert.equal(out.includes("tok/en+123"), false);
  assert.equal(out.includes("tok%2Fen%2B123"), false);
  assert.equal(out.includes("s3cr3tvalue"), false);
  assert.match(out, /\[REDACTED:ORATS_API_TOKEN\]/);
  assert.match(out, /\[REDACTED:ALPACA_API_SECRET\]/);
});

test("a secret is redacted however a URL encoder wrote it", () => {
  const env = { ORATS_API_TOKEN: "ab!1:cd/34+ef 56" };
  const written = [
    "ab!1%3Acd/34%2Bef%2056", // Python urllib quote
    "ab%211%3Acd%2F34%2Bef+56", // httpx query parameter
    "ab!1%3acd%2f34%2bef%2056", // lower-case hex
    "ab!1:cd/34+ef 56",
  ];
  const out = redactSecrets(written.map((form) => `token=${form}&x=1`).join("\n"), env);
  assert.equal(
    out,
    written.map(() => "token=[REDACTED:ORATS_API_TOKEN]&x=1").join("\n"),
  );
});

test("text that only resembles a secret is left alone", () => {
  const env = { ORATS_API_TOKEN: "a.c+" };
  assert.equal(redactSecrets("abc+ a.cc a.c", env), "abc+ a.cc a.c");
});
