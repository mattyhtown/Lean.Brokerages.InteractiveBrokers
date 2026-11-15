import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, utimes, writeFile } from "node:fs/promises";
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
function deps(fake: FakeEngine): OperationDeps {
  return {
    engineDir,
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

test("research_ingest names each source once, however often it was asked for", async () => {
  const result = await researchIngest(
    { sources: ["uw", "orats", "uw"], days: 5 },
    deps(() => ({ stdout: JSON.stringify({ uw: {}, orats: {} }, null, 2) })),
  );
  assert.deepEqual(calls[0]?.args, ["ingest", "--source", "uw", "--source", "orats", "--days", "5"]);
  assert.ok(result.ok);
  assert.deepEqual(result.sourcesRequested, ["uw", "orats"]);
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
  // An earlier source in the same run may already have written its files.
  assert.equal("cacheMayHaveChanged" in result && result.cacheMayHaveChanged, true);
});

test("research_ingest that never started cannot have changed the cache", async () => {
  const result = await researchIngest(
    { sources: ["alpaca"] },
    deps(() => ({ exitCode: null, spawnError: "spawn python3 ENOENT" })),
  );
  assert.ok(!result.ok);
  assert.equal("cacheMayHaveChanged" in result && result.cacheMayHaveChanged, false);
});

test("a date range the engine should not walk is refused before it starts", async () => {
  const never = deps(() => ({}));
  const refused = [
    await researchIngest({ sources: ["uw"], start: "2026-11-05", end: "2026-11-02" }, never),
    await researchIngest({ sources: ["uw"], start: "1900-01-01", end: "2026-11-05" }, never),
    // No end: the engine would run from 1900 to today.
    await researchIngest({ sources: ["uw"], start: "1900-01-01" }, never),
    await researchIngest({ sources: ["uw"], end: "2026-02-30" }, never),
    await backtest({ ...backtestInput, from: "2026-11-05", to: "2026-11-02" }, never),
    await backtest({ ...backtestInput, from: "2020-01-01", to: "2026-11-05" }, never),
    await backtest({ ...backtestInput, from: "2026-13-01" }, never),
  ];
  const details = refused.map((result) => {
    assert.ok(!result.ok);
    assert.equal(result.error, "invalid_input");
    return result.detail;
  });
  assert.match(details[0]!, /2026-11-05 is after 2026-11-02/);
  assert.match(details[1]!, /the limit is 400/);
  assert.match(details[3]!, /2026-02-30 is not a calendar date/);
  assert.equal(calls.length, 0);
});

test("a range of exactly the limit, and one starting today, are allowed", async () => {
  const today = new Date().toISOString().slice(0, 10);
  const ok = deps(() => ({ stdout: JSON.stringify({ uw: {} }, null, 2) }));
  assert.ok((await researchIngest({ sources: ["uw"], start: "2025-10-01", end: "2026-11-05" }, ok)).ok);
  assert.ok((await researchIngest({ sources: ["uw"], start: today }, ok)).ok);
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

test("research_evaluate reports the run-1 holdout size it used when none was given", async () => {
  const result = await researchEvaluate(
    { run: 1 },
    deps(async (request) => {
      await writeFile(argAfter(request, "--report"), "# Research\n");
      return {};
    }),
  );
  assert.equal(argAfter(calls[0]!, "--holdout-days"), "8");
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

const COVERAGE = {
  from: "2026-09-24",
  to: "2026-10-21",
  sessions: 20,
  simulated_sessions: 92,
  no_local_spot_sessions: 3,
  no_local_spot_handling: "alpaca_daily_bar_or_skipped",
  research_chain_sessions: 5,
  cached_chain_sessions: 2,
  no_chain_sessions: 10,
  no_chain_substitute: "alpaca_current_snapshot",
};

/** A backtest CLI that writes `report`, behind a coverage check that prints `coverage`. */
function backtestEngine(
  report: unknown,
  run: Partial<EngineRun> = {},
  coverage: unknown = COVERAGE,
): FakeEngine {
  return async (request) => {
    if (request.module === "odte_spy_ibkr.hosted_state") {
      return coverage === null ? { exitCode: 1 } : { stdout: `${JSON.stringify(coverage)}\n` };
    }
    if (report !== undefined) {
      await writeFile(argAfter(request, "--report-json"), JSON.stringify(report));
    }
    return run;
  };
}

test("backtest passes the rolling Sharpe requirement and reports a pass", async () => {
  const result = await backtest(
    backtestInput,
    deps(backtestEngine({ max_rolling_sharpe: 1.8, trade_count: 12 })),
  );
  // Coverage is read first: the backtest caches the chains it fetches. It
  // is asked for the sessions this walk-forward backtest scores.
  assert.deepEqual(calls[0], {
    module: "odte_spy_ibkr.hosted_state",
    args: ["--chain-coverage", "2026-07-01", "2026-11-05", "--walk-forward"],
    timeoutMs: 60_000,
  });
  assert.equal(calls[1]?.module, "odte_spy_ibkr.backtest");
  assert.equal(argAfter(calls[1]!, "--require-rolling-sharpe"), "1.5");
  assert.ok(calls[1]?.args.includes("--walk-forward"));
  assert.equal(calls[1]?.args.includes("--verbose"), false);
  assert.ok(result.ok);
  assert.deepEqual(result.sharpeCheck, { requiredRollingSharpe: 1.5, passed: true });
  assert.equal(result.summary.max_rolling_sharpe, 1.8);
  assert.deepEqual(result.chainCoverage, COVERAGE);
});

test("a backtest that scores every session asks for coverage of every session", async () => {
  await backtest(
    { ...backtestInput, walkForward: false },
    deps(backtestEngine({ max_rolling_sharpe: 1.8 })),
  );
  assert.deepEqual(calls[0]?.args, ["--chain-coverage", "2026-07-01", "2026-11-05"]);
  assert.equal(calls[1]?.args.includes("--walk-forward"), false);
});

test("backtest exit 1 with a report is a failed Sharpe check, not an error", async () => {
  const result = await backtest(
    backtestInput,
    deps(backtestEngine({ max_rolling_sharpe: 0.4 }, { exitCode: 1 })),
  );
  assert.ok(result.ok);
  assert.equal(result.sharpeCheck.passed, false);
});

test("backtest still reports when chain coverage could not be read, as unknown", async () => {
  const result = await backtest(
    backtestInput,
    deps(backtestEngine({ max_rolling_sharpe: 1.8 }, {}, null)),
  );
  assert.ok(result.ok);
  assert.equal(result.chainCoverage, null);
});

test("backtest exit 1 without a report is an engine failure", async () => {
  const result = await backtest(
    backtestInput,
    deps(
      backtestEngine(undefined, {
        exitCode: 1,
        stderr: "Traceback (most recent call last):\nKeyError: 'close'",
      }),
    ),
  );
  assert.ok(!result.ok);
  assert.equal(result.error, "engine_failed");
});

test("backtest keeps only the most recent sessions of a long verbose run", async () => {
  const sessions = Array.from({ length: 90 }, (_, i) => ({ date: `d${i}`, reason: "no_entry" }));
  const result = await backtest(
    { ...backtestInput, verbose: true },
    deps(backtestEngine({ sessions })),
  );
  assert.ok(result.ok);
  assert.equal(result.sessionsTruncated, true);
  assert.equal((result.summary.sessions as unknown[]).length, 60);
});

test("engine jobs of different kinds never overlap on the shared cache", async () => {
  let active = 0;
  let maxActive = 0;
  const engine: FakeEngine = async (request) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    await new Promise((resolve) => setTimeout(resolve, 10));
    active -= 1;
    if (request.module === "odte_spy_ibkr.backtest") {
      await writeFile(argAfter(request, "--report-json"), "{}");
    }
    if (request.args.includes("--report")) {
      await writeFile(argAfter(request, "--report"), "# Research\n");
    }
    return { stdout: JSON.stringify({ uw: {} }, null, 2) };
  };
  const shared = deps(engine);
  const results = await Promise.all([
    researchIngest({ sources: ["uw"], days: 5 }, shared),
    backtest(backtestInput, shared),
    researchEvaluate({ run: 2 }, shared),
    researchIngest({ sources: ["orats"], days: 5 }, shared),
  ]);
  assert.deepEqual(results.map((result) => result.ok), [true, true, true, true]);
  assert.equal(maxActive, 1);
  // Started in the order they were asked for.
  assert.deepEqual(
    calls.map((call) => call.module.replace("odte_spy_ibkr.", "")),
    ["research", "hosted_state", "backtest", "research", "research"],
  );
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
      return { stdout: "No promotion: paper_auto_promote_disabled\n" };
    }
    if (request.args[0] === "--improvement-chain-coverage") {
      return { stdout: `${JSON.stringify(COVERAGE)}\n` };
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
    {
      module: "odte_spy_ibkr.hosted_state",
      args: ["--improvement-chain-coverage"],
      timeoutMs: 60_000,
    },
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
  assert.deepEqual(result.chainCoverage, COVERAGE);
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

test("improvement runs queue, so each one reports its own history record", async () => {
  await seedParams();
  let active = 0;
  let maxActive = 0;
  let serial = 0;
  const engine: FakeEngine = async (request) => {
    if (request.args[0] === "--improvement-chain-coverage") {
      return { exitCode: 1 };
    }
    if (request.module !== "odte_spy_ibkr.improvement") {
      const record = JSON.parse(await readFile(argAfter(request, "--promotion-check"), "utf8"));
      return { stdout: JSON.stringify({ reason: `decision_for_${record.best_candidate}` }) };
    }
    active += 1;
    maxActive = Math.max(maxActive, active);
    serial += 1;
    const mine = serial;
    await new Promise((resolve) => setTimeout(resolve, 20));
    await writeFile(
      path.join(engineDir, "params", "history", `20261115T09000${mine}Z.json`),
      JSON.stringify({ best_candidate: `candidate_${mine}` }),
    );
    active -= 1;
    return {};
  };
  const shared = deps(engine);
  const [first, second] = await Promise.all([improvementRun(shared), improvementRun(shared)]);

  assert.equal(maxActive, 1);
  assert.ok(first.ok && second.ok);
  assert.match(first.historyFile, /20261115T090001Z\.json$/);
  assert.equal(first.decisionReason, "decision_for_candidate_1");
  assert.match(second.historyFile, /20261115T090002Z\.json$/);
  assert.equal(second.decisionReason, "decision_for_candidate_2");
});

test("improvement_run refuses to guess when two history records changed", async () => {
  await seedParams();
  const result = await improvementRun(
    deps(async (request) => {
      if (request.module !== "odte_spy_ibkr.improvement") {
        return { exitCode: 1 };
      }
      for (const name of ["20261115T090000Z.json", "20261115T090001Z.json"]) {
        await writeFile(path.join(engineDir, "params", "history", name), "{}");
      }
      return {};
    }),
  );
  assert.ok(!result.ok);
  assert.equal(result.error, "bad_engine_output");
  assert.match(result.detail, /2 history records changed/);
  assert.equal(
    calls.some((call) => call.args[0] === "--promotion-check"),
    false,
    "no promotion check on a record that may not be this run's",
  );
});

test("improvement_run recognizes a record rewritten under the same second's name", async () => {
  await seedParams();
  const existing = path.join(engineDir, "params", "history", HISTORY_NAME);
  await writeFile(existing, JSON.stringify({ best_candidate: "earlier" }));
  await utimes(existing, new Date("2026-11-15T09:00:00Z"), new Date("2026-11-15T09:00:00Z"));
  const result = await improvementRun(
    deps(
      improvementEngine({
        record: { best_candidate: "later" },
        decision: { reason: "below_sharpe_target", would_promote: false },
      }),
    ),
  );
  assert.ok(result.ok);
  assert.deepEqual(result.historyRecord, { best_candidate: "later" });
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
