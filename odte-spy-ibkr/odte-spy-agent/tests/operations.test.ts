import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, test } from "node:test";
import type { EngineRequest, EngineRun } from "../bot/lib/engine.js";
import {
  backtest,
  improvementRun,
  researchEvaluate,
  researchIngest,
  signalState,
  type OperationDeps,
} from "../bot/lib/operations.js";

type FakeEngine = (request: EngineRequest) => Promise<Partial<EngineRun>> | Partial<EngineRun>;

let engineDir: string;
let calls: EngineRequest[];

beforeEach(async () => {
  engineDir = await mkdtemp(path.join(os.tmpdir(), "odte-agent-test-"));
  calls = [];
});

afterEach(async () => {
  await rm(engineDir, { recursive: true, force: true });
});

/** Stands in for the Python engine. No process is started. */
function deps(fake: FakeEngine, env: NodeJS.ProcessEnv = {}): OperationDeps {
  return {
    engineDir,
    env,
    run: async (request) => {
      calls.push(request);
      return {
        exitCode: 0,
        stdout: "",
        stderr: "",
        timedOut: false,
        spawnError: null,
        ...(await fake(request)),
      };
    },
  };
}

function argAfter(request: EngineRequest, flag: string): string {
  const value = request.args[request.args.indexOf(flag) + 1];
  assert.ok(value !== undefined, `${flag} was not passed`);
  return value;
}

test("research_ingest runs the research CLI and summarizes what it wrote", async () => {
  const stdout = [
    "2026-11-15 08:00:00 | INFO    | odte_spy_ibkr.research.ingest | Research credential scan",
    JSON.stringify(
      {
        alpaca: { daily: "data/research/alpaca/spy_daily.json", "2026-11-02": "a.json" },
        uw: { days_flow: 2, days_chain: 0, errors: [] },
      },
      null,
      2,
    ),
  ].join("\n");
  const result = await researchIngest(
    { sources: ["uw"], start: "2026-11-02", end: "2026-11-05" },
    deps(() => ({ stdout })),
  );

  assert.deepEqual(calls[0]?.module, "odte_spy_ibkr.research");
  assert.deepEqual(calls[0]?.args, [
    "ingest",
    "--source",
    "uw",
    "--start",
    "2026-11-02",
    "--end",
    "2026-11-05",
  ]);
  assert.equal(result.ok, true);
  assert.ok(result.ok);
  // What ran comes from the engine's output, not from what was asked for.
  assert.deepEqual(result.sourcesRequested, ["uw"]);
  assert.deepEqual(result.sourcesRun, ["alpaca", "uw"]);
  assert.deepEqual(result.sources[0], {
    source: "alpaca",
    entryCount: 2,
    summary: { daily: "data/research/alpaca/spy_daily.json", "2026-11-02": "a.json" },
    summaryTruncated: false,
  });
  // A stats object is passed through as the engine reported it.
  assert.deepEqual(result.sources[1]?.summary, { days_flow: 2, days_chain: 0, errors: [] });
});

test("research_ingest caps a long path map", async () => {
  const paths = Object.fromEntries(
    Array.from({ length: 40 }, (_, i) => [`2026-09-${String(i + 1).padStart(2, "0")}`, `m${i}.json`]),
  );
  const result = await researchIngest(
    { sources: ["alpaca"], days: 60 },
    deps(() => ({ stdout: JSON.stringify({ alpaca: paths }, null, 2) })),
  );
  assert.deepEqual(calls[0]?.args, ["ingest", "--source", "alpaca", "--days", "60"]);
  assert.ok(result.ok);
  assert.equal(result.sources[0]?.entryCount, 40);
  assert.equal(Object.keys(result.sources[0]?.summary ?? {}).length, 12);
  assert.equal(result.sources[0]?.summaryTruncated, true);
});

test("research_ingest reports a missing credential as a failure with the log tail", async () => {
  const result = await researchIngest(
    { sources: ["alpaca"] },
    deps(() => ({
      exitCode: 1,
      stderr: "Traceback (most recent call last):\nRuntimeError: ALPACA_API_KEY and ALPACA_API_SECRET are required",
    })),
  );
  assert.equal(result.ok, false);
  assert.ok(!result.ok);
  assert.equal(result.error, "engine_failed");
  assert.match(result.logTail.join("\n"), /ALPACA_API_KEY and ALPACA_API_SECRET are required/);
});

test("a host without Python or the engine dependencies is reported as engine_unavailable", async () => {
  const noPython = await signalState(deps(() => ({ exitCode: null, spawnError: "spawn python3 ENOENT" })));
  assert.ok(!noPython.ok);
  assert.equal(noPython.error, "engine_unavailable");

  const noDeps = await signalState(
    deps(() => ({ exitCode: 1, stderr: "ModuleNotFoundError: No module named 'pydantic'" })),
  );
  assert.ok(!noDeps.ok);
  assert.equal(noDeps.error, "engine_unavailable");
});

test("a job killed at its time limit is reported as a timeout", async () => {
  const result = await improvementRun(deps(() => ({ exitCode: null, timedOut: true })));
  assert.ok(!result.ok);
  assert.equal(result.error, "timeout");
});

test("research_evaluate defaults to the engine's fixed run-2 holdout", async () => {
  const result = await researchEvaluate(
    { run: 2, holdoutDays: 30 },
    deps(async (request) => {
      await writeFile(argAfter(request, "--report"), "# Research\nno holdout survivor\n");
      return {};
    }),
  );
  // --holdout-days is not sent for run 2: the engine would ignore it.
  assert.deepEqual(calls[0]?.args.slice(0, 3), ["evaluate", "--run", "2"]);
  assert.ok(!calls[0]?.args.includes("--holdout-days"));
  assert.ok(result.ok);
  assert.equal(result.run, 2);
  assert.equal(result.holdoutDays, null);
  assert.match(result.reportMarkdown, /no holdout survivor/);
  assert.equal(result.reportTruncated, false);
});

test("research_evaluate passes the tail holdout size for run 1", async () => {
  const result = await researchEvaluate(
    { run: 1, holdoutDays: 8 },
    deps(async (request) => {
      await writeFile(argAfter(request, "--report"), "# Research\n");
      return {};
    }),
  );
  assert.deepEqual(calls[0]?.args.slice(0, 5), ["evaluate", "--run", "1", "--holdout-days", "8"]);
  assert.ok(result.ok);
  assert.equal(result.holdoutDays, 8);
});

test("research_evaluate fails when the cache is too thin for the engine to run", async () => {
  const result = await researchEvaluate(
    { run: 2 },
    deps(() => ({ exitCode: 1, stderr: "RuntimeError: Insufficient real minute-bar cache" })),
  );
  assert.ok(!result.ok);
  assert.match(result.logTail.join("\n"), /Insufficient real minute-bar cache/);
});

const backtestInput = {
  from: "2026-07-01",
  to: "2026-11-05",
  requireRollingSharpe: 1.5,
  walkForward: true,
  verbose: false,
};

test("backtest passes the rolling Sharpe requirement and reports a pass", async () => {
  const result = await backtest(
    backtestInput,
    deps(
      async (request) => {
        await writeFile(
          argAfter(request, "--report-json"),
          JSON.stringify({ max_rolling_sharpe: 1.8, trade_count: 12 }),
        );
        return { exitCode: 0 };
      },
      { ALPACA_API_KEY: "k", ALPACA_API_SECRET: "s" },
    ),
  );
  assert.equal(calls[0]?.module, "odte_spy_ibkr.backtest");
  assert.equal(argAfter(calls[0]!, "--require-rolling-sharpe"), "1.5");
  assert.ok(calls[0]?.args.includes("--walk-forward"));
  assert.equal(calls[0]?.args.includes("--verbose"), false);
  assert.ok(result.ok);
  assert.deepEqual(result.sharpeCheck, { requiredRollingSharpe: 1.5, passed: true });
  assert.equal(result.summary.max_rolling_sharpe, 1.8);
  assert.equal(result.alpacaKeysPresent, true);
});

test("backtest exit 1 with a report is a failed Sharpe check, not an error", async () => {
  const result = await backtest(
    backtestInput,
    deps(async (request) => {
      await writeFile(argAfter(request, "--report-json"), JSON.stringify({ max_rolling_sharpe: 0.4 }));
      return { exitCode: 1 };
    }),
  );
  assert.ok(result.ok);
  assert.equal(result.sharpeCheck.passed, false);
  assert.equal(result.alpacaKeysPresent, false);
});

test("backtest exit 1 without a report is an engine failure", async () => {
  const result = await backtest(
    backtestInput,
    deps(() => ({ exitCode: 1, stderr: "Traceback (most recent call last):\nKeyError: 'close'" })),
  );
  assert.ok(!result.ok);
  assert.equal(result.error, "engine_failed");
});

test("backtest keeps only the most recent sessions of a long verbose run", async () => {
  const sessions = Array.from({ length: 90 }, (_, i) => ({ date: `d${i}`, reason: "no_entry" }));
  const result = await backtest(
    { ...backtestInput, verbose: true },
    deps(async (request) => {
      await writeFile(argAfter(request, "--report-json"), JSON.stringify({ sessions }));
      return {};
    }),
  );
  assert.ok(result.ok);
  assert.equal(result.sessionsTruncated, true);
  assert.equal((result.summary.sessions as unknown[]).length, 60);
});

async function seedParams(): Promise<string> {
  const paramsDir = path.join(engineDir, "params");
  await mkdir(path.join(paramsDir, "history"), { recursive: true });
  await writeFile(path.join(paramsDir, "history", "20261101T000000Z.json"), "{}");
  const activePath = path.join(paramsDir, "active.yaml");
  await writeFile(activePath, "version: 1\n");
  return activePath;
}

const HISTORY_NAME = "20261115T090000Z.json";

function improvementEngine(options: {
  record?: Record<string, unknown>;
  decision?: Record<string, unknown> | "crash";
  duringRun?: () => Promise<void>;
}): FakeEngine {
  return async (request) => {
    if (request.module === "odte_spy_ibkr.improvement") {
      await writeFile(
        path.join(engineDir, "params", "history", HISTORY_NAME),
        JSON.stringify(options.record ?? {}),
      );
      await options.duringRun?.();
      // The real CLI prints nothing on a successful run.
      return {};
    }
    if (options.decision === "crash" || options.decision === undefined) {
      return { exitCode: 1, stderr: "Traceback (most recent call last):\nValueError" };
    }
    return { stdout: `${JSON.stringify(options.decision)}\n` };
  };
}

test("improvement_run returns the new history record and leaves active params alone", async () => {
  const activePath = await seedParams();
  const record = { best_candidate: "candidate_3", best_sharpe: 1.9, best_score: 1.7 };
  const decision = {
    reason: "paper_auto_promote_disabled",
    would_promote: false,
    challenger_sharpe: 1.9,
    active_sharpe: null,
    sharpe_target: 1.5,
    sharpe_margin: 0.1,
  };
  const result = await improvementRun(deps(improvementEngine({ record, decision })));
  assert.deepEqual(calls, [
    { module: "odte_spy_ibkr.improvement", args: ["run"], timeoutMs: 15 * 60_000 },
    {
      module: "odte_spy_ibkr.hosted_state",
      args: ["--promotion-check", path.join(engineDir, "params", "history", HISTORY_NAME)],
      timeoutMs: 60_000,
    },
  ]);
  assert.ok(result.ok);
  assert.deepEqual(result.historyRecord, record);
  assert.equal(result.historyFile, path.join(engineDir, "params", "history", HISTORY_NAME));
  assert.deepEqual(result.decision, decision);
  assert.equal(result.decisionReason, "paper_auto_promote_disabled");
  assert.equal(result.qualifiedForPaperPromotion, true);
  assert.equal(result.activeParamsChanged, false);
  assert.equal(await readFile(activePath, "utf8"), "version: 1\n");
});

test("improvement_run reports a challenger below the Sharpe target as not qualified", async () => {
  await seedParams();
  const result = await improvementRun(
    deps(improvementEngine({ decision: { reason: "below_sharpe_target", would_promote: false } })),
  );
  assert.ok(result.ok);
  assert.equal(result.decisionReason, "below_sharpe_target");
  assert.equal(result.qualifiedForPaperPromotion, false);
});

test("improvement_run says the decision is unknown when the check fails, not that it failed to qualify", async () => {
  await seedParams();
  const result = await improvementRun(deps(improvementEngine({ decision: "crash" })));
  assert.ok(result.ok);
  assert.equal(result.decision, null);
  assert.equal(result.decisionReason, null);
  assert.equal(result.qualifiedForPaperPromotion, null);
});

test("improvement_run flags a changed active params file", async () => {
  const activePath = await seedParams();
  const result = await improvementRun(
    deps(
      improvementEngine({
        decision: { reason: "promoted", would_promote: true },
        duringRun: () => writeFile(activePath, "version: 2\n"),
      }),
    ),
  );
  assert.ok(result.ok);
  assert.equal(result.activeParamsChanged, true);
  assert.equal(result.decisionReason, "promoted");
  assert.equal(result.qualifiedForPaperPromotion, false);
});

test("improvement_run without a new history record is a failure", async () => {
  await seedParams();
  const result = await improvementRun(deps(() => ({})));
  assert.ok(!result.ok);
  assert.equal(result.error, "bad_engine_output");
});

test("signal_state returns the engine's JSON and keeps unavailable fields null", async () => {
  const state = {
    ib_session: { available: false, reason: "no TWS/Gateway session" },
    signal: { spot: 601.2, net_call_bias: null },
    entry_intent: null,
  };
  const result = await signalState(deps(() => ({ stdout: `${JSON.stringify(state)}\n` })));
  assert.deepEqual(calls[0]?.module, "odte_spy_ibkr.hosted_state");
  assert.ok(result.ok);
  assert.deepEqual(result.state, state);
});

test("signal_state rejects output that is not JSON", async () => {
  const result = await signalState(deps(() => ({ stdout: "Outside RTH; sleeping" })));
  assert.ok(!result.ok);
  assert.equal(result.error, "bad_engine_output");
});
