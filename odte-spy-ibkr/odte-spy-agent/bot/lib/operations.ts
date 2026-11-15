import { mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  createEngineRunner,
  resolveEngineDir,
  type EngineRun,
  type EngineRunner,
} from "./engine.js";

export type OperationDeps = {
  run: EngineRunner;
  engineDir: string;
};

export function defaultDeps(): OperationDeps {
  return {
    run: createEngineRunner(process.env),
    engineDir: resolveEngineDir(process.env),
  };
}

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

type Failure = {
  ok: false;
  error: "invalid_input" | "engine_unavailable" | "timeout" | "engine_failed" | "bad_engine_output";
  detail: string;
  exitCode: number | null;
  logTail: string[];
};

const LOG_TAIL_LINES = 40;
const MAX_REPORT_CHARS = 20_000;
const MAX_SESSIONS = 60;
const MAX_SUMMARY_ENTRIES = 12;
const MAX_RANGE_DAYS = 400;
const DAY_MS = 86_400_000;
// What the engine uses for run 1 when --holdout-days is omitted. Passing it
// keeps the size this reports the size the engine used.
const RUN1_DEFAULT_HOLDOUT_DAYS = 8;

function tail(text: string, lines = LOG_TAIL_LINES): string[] {
  return text
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0)
    .slice(-lines);
}

function logTail(run: EngineRun): string[] {
  return tail(`${run.stdout}\n${run.stderr}`);
}

function failure(run: EngineRun, error: Failure["error"], detail: string): Failure {
  return { ok: false, error, detail, exitCode: run.exitCode, logTail: logTail(run) };
}

/** Classifies a run that did not produce its result. */
function failureOf(run: EngineRun): Failure {
  if (run.spawnError !== null) {
    return failure(run, "engine_unavailable", `could not start the engine: ${run.spawnError}`);
  }
  if (run.timedOut) {
    return failure(run, "timeout", "the engine process was killed at its time limit");
  }
  if (/ModuleNotFoundError|No module named/.test(run.stderr)) {
    return failure(
      run,
      "engine_unavailable",
      "Python started but the engine or one of its dependencies is not installed on this host",
    );
  }
  return failure(run, "engine_failed", `engine exited with code ${String(run.exitCode)}`);
}

function parseJsonObject(text: string): Record<string, Json> | null {
  try {
    const parsed: unknown = JSON.parse(text);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, Json>)
      : null;
  } catch {
    return null;
  }
}

/**
 * The research CLI prints log lines, then one JSON object indented by two
 * spaces, so the only line that is exactly "{" opens that object.
 */
function parseTrailingJsonObject(stdout: string): Record<string, Json> | null {
  const lines = stdout.split("\n");
  const start = lines.lastIndexOf("{");
  return start === -1 ? null : parseJsonObject(lines.slice(start).join("\n"));
}

/** A request refused before any engine process is started. */
function invalidInput(detail: string): Failure {
  return { ok: false, error: "invalid_input", detail, exitCode: null, logTail: [] };
}

function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Milliseconds for a YYYY-MM-DD that names a real day, else null. */
function parseDay(value: string): number | null {
  const ms = Date.parse(`${value}T00:00:00Z`);
  // Date.parse rolls 2026-02-30 over into March; a real day round-trips.
  return Number.isNaN(ms) || isoDay(ms) !== value ? null : ms;
}

/** Why the engine should not be asked to walk start..end, or null. */
function rangeIssue(start: string, end: string): string | null {
  const from = parseDay(start);
  const to = parseDay(end);
  if (from === null || to === null) {
    return `${from === null ? start : end} is not a calendar date`;
  }
  if (from > to) {
    return `${start} is after ${end}`;
  }
  const days = Math.round((to - from) / DAY_MS);
  return days > MAX_RANGE_DAYS
    ? `${start} to ${end} spans ${days} days; the limit is ${MAX_RANGE_DAYS}`
    : null;
}

// Engine jobs read and write the same cache files with no locking, and the
// engine names an improvement record by the second. So the jobs this process
// starts run one at a time. Jobs started by another process are not covered.
let engineQueue: Promise<unknown> = Promise.resolve();

function queued<T>(job: () => Promise<T>): Promise<T> {
  const result = engineQueue.then(job);
  engineQueue = result.catch(() => undefined);
  return result;
}

/**
 * Where the sessions a job scores will get their option chains. Asked before
 * the job, because a backtest caches the chains it fetches. null: not known.
 */
async function chainCoverage(deps: OperationDeps, args: string[]) {
  const run = await deps.run({ module: "odte_spy_ibkr.hosted_state", args, timeoutMs: 60_000 });
  return run.exitCode === 0 ? parseJsonObject(run.stdout) : null;
}

async function withTempDir<T>(fn: (dir: string) => Promise<T>): Promise<T> {
  const dir = await mkdtemp(path.join(os.tmpdir(), "odte-agent-"));
  try {
    return await fn(dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

async function readIfExists(file: string): Promise<string | null> {
  try {
    return await readFile(file, "utf8");
  } catch {
    return null;
  }
}

async function listIfExists(dir: string): Promise<string[]> {
  try {
    return await readdir(dir);
  } catch {
    return [];
  }
}

export type IngestSource = "alpaca" | "uw" | "orats" | "tradingeconomics" | "quantconnect";

export type ResearchIngestInput = {
  sources: IngestSource[];
  start?: string;
  end?: string;
  days?: number;
};

function ingestRangeIssue(input: ResearchIngestInput): string | null {
  if (input.start === undefined) {
    return input.end !== undefined && parseDay(input.end) === null
      ? `${input.end} is not a calendar date`
      : null;
  }
  // The engine's default end is its local today, at most a day past UTC's.
  return rangeIssue(input.start, input.end ?? isoDay(Date.now() + DAY_MS));
}

export async function researchIngest(input: ResearchIngestInput, deps = defaultDeps()) {
  const issue = ingestRangeIssue(input);
  if (issue !== null) {
    return invalidInput(issue);
  }
  return queued(() => runIngest(input, deps));
}

async function runIngest(input: ResearchIngestInput, deps: OperationDeps) {
  // The engine runs a source once per mention.
  const requested = [...new Set(input.sources)];
  const args = ["ingest", ...requested.flatMap((source) => ["--source", source])];
  if (input.start !== undefined) {
    args.push("--start", input.start);
  }
  if (input.end !== undefined) {
    args.push("--end", input.end);
  }
  if (input.days !== undefined) {
    args.push("--days", String(input.days));
  }
  const run = await deps.run({
    module: "odte_spy_ibkr.research",
    args,
    timeoutMs: 20 * 60_000,
  });
  if (run.exitCode !== 0) {
    // Sources run in order and each writes as it goes, so one that finished
    // before the failure has already changed the cache.
    return { ...failureOf(run), cacheMayHaveChanged: run.spawnError === null };
  }
  const written = parseTrailingJsonObject(run.stdout);
  if (written === null) {
    return failure(run, "bad_engine_output", "ingest exited 0 without the JSON path summary");
  }
  // Sources report differently: alpaca and orats return a date -> path map,
  // the others a stats object. Pass either through, capped.
  const sources = Object.entries(written).map(([source, value]) => {
    const entries =
      typeof value === "object" && value !== null && !Array.isArray(value)
        ? Object.entries(value)
        : [];
    return {
      source,
      entryCount: entries.length,
      summary: Object.fromEntries(entries.slice(0, MAX_SUMMARY_ENTRIES)) as Record<string, Json>,
      summaryTruncated: entries.length > MAX_SUMMARY_ENTRIES,
    };
  });
  return {
    ok: true as const,
    sourcesRequested: requested,
    sourcesRun: sources.map((entry) => entry.source),
    sources,
    cacheDir: path.join(deps.engineDir, "data", "research"),
  };
}

export type ResearchEvaluateInput = { run: 1 | 2; holdoutDays?: number };

export function researchEvaluate(input: ResearchEvaluateInput, deps = defaultDeps()) {
  return queued(() => runEvaluate(input, deps));
}

function runEvaluate(input: ResearchEvaluateInput, deps: OperationDeps) {
  return withTempDir(async (dir) => {
    const reportPath = path.join(dir, "research-report.md");
    // The engine ignores --holdout-days for run 2, whose holdout is fixed.
    const holdoutDays =
      input.run === 1 ? (input.holdoutDays ?? RUN1_DEFAULT_HOLDOUT_DAYS) : null;
    const args = ["evaluate", "--run", String(input.run)];
    if (holdoutDays !== null) {
      args.push("--holdout-days", String(holdoutDays));
    }
    args.push("--report", reportPath);
    const run = await deps.run({
      module: "odte_spy_ibkr.research",
      args,
      timeoutMs: 5 * 60_000,
    });
    if (run.exitCode !== 0) {
      return failureOf(run);
    }
    const report = await readIfExists(reportPath);
    if (report === null) {
      return failure(run, "bad_engine_output", "evaluate exited 0 without writing its report");
    }
    return {
      ok: true as const,
      run: input.run,
      holdoutDays,
      reportMarkdown: report.slice(0, MAX_REPORT_CHARS),
      reportTruncated: report.length > MAX_REPORT_CHARS,
    };
  });
}

export type BacktestInput = {
  from: string;
  to: string;
  requireRollingSharpe: number;
  walkForward: boolean;
  verbose: boolean;
};

export async function backtest(input: BacktestInput, deps = defaultDeps()) {
  const issue = rangeIssue(input.from, input.to);
  if (issue !== null) {
    return invalidInput(issue);
  }
  return queued(() => runBacktest(input, deps));
}

function runBacktest(input: BacktestInput, deps: OperationDeps) {
  return withTempDir(async (dir) => {
    const coverage = await chainCoverage(deps, [
      "--chain-coverage",
      input.from,
      input.to,
      ...(input.walkForward ? ["--walk-forward"] : []),
    ]);
    const reportPath = path.join(dir, "backtest.json");
    const args = [
      "--from",
      input.from,
      "--to",
      input.to,
      "--report-json",
      reportPath,
      "--require-rolling-sharpe",
      String(input.requireRollingSharpe),
    ];
    if (input.walkForward) {
      args.push("--walk-forward");
    }
    if (input.verbose) {
      args.push("--verbose");
    }
    const run = await deps.run({
      module: "odte_spy_ibkr.backtest",
      args,
      timeoutMs: 10 * 60_000,
    });
    // The CLI exits 1 both when the Sharpe check fails and when it crashes.
    // Only a written report proves the backtest itself finished.
    const reportText = await readIfExists(reportPath);
    if (reportText === null || (run.exitCode !== 0 && run.exitCode !== 1)) {
      return failureOf(run);
    }
    let summary: Record<string, Json>;
    try {
      summary = JSON.parse(reportText) as Record<string, Json>;
    } catch {
      return failure(run, "bad_engine_output", "the backtest report is not valid JSON");
    }
    const sessions = Array.isArray(summary.sessions) ? summary.sessions : null;
    const sessionsTruncated = sessions !== null && sessions.length > MAX_SESSIONS;
    if (sessions !== null && sessionsTruncated) {
      summary.sessions = sessions.slice(-MAX_SESSIONS);
    }
    return {
      ok: true as const,
      sharpeCheck: {
        requiredRollingSharpe: input.requireRollingSharpe,
        passed: run.exitCode === 0,
      },
      summary,
      sessionsTruncated,
      chainCoverage: coverage,
    };
  });
}

/** History file name -> modification time. */
async function historySnapshot(dir: string): Promise<Map<string, number>> {
  const snapshot = new Map<string, number>();
  for (const name of await listIfExists(dir)) {
    try {
      snapshot.set(name, (await stat(path.join(dir, name))).mtimeMs);
    } catch {
      // Removed between the listing and the stat.
    }
  }
  return snapshot;
}

export function improvementRun(deps = defaultDeps()) {
  return queued(() => runImprovement(deps));
}

// The engine names a history record by the second and prints nothing, so a
// record is matched to its run by what changed on disk. That only holds for
// one run at a time, which the queue gives within this process.
async function runImprovement(deps: OperationDeps) {
  const historyDir = path.join(deps.engineDir, "params", "history");
  const activePath = path.join(deps.engineDir, "params", "active.yaml");
  const coverage = await chainCoverage(deps, ["--improvement-chain-coverage"]);
  const historyBefore = await historySnapshot(historyDir);
  const activeBefore = await readIfExists(activePath);

  const run = await deps.run({
    module: "odte_spy_ibkr.improvement",
    args: ["run"],
    timeoutMs: 15 * 60_000,
  });
  if (run.exitCode !== 0) {
    return failureOf(run);
  }

  // Written by this run: a new name, or a same-second name written again.
  const written = [...(await historySnapshot(historyDir))]
    .filter(([name, mtimeMs]) => historyBefore.get(name) !== mtimeMs)
    .map(([name]) => name);
  if (written.length > 1) {
    return failure(
      run,
      "bad_engine_output",
      `${written.length} history records changed during the run (${written.sort().join(", ")}); ` +
        "another process is writing params/history, so this run's record cannot be identified",
    );
  }
  const historyFile = written[0] ?? null;
  let historyRecord: Record<string, Json> | null = null;
  if (historyFile !== null) {
    const text = await readIfExists(path.join(historyDir, historyFile));
    try {
      historyRecord = text === null ? null : (JSON.parse(text) as Record<string, Json>);
    } catch {
      historyRecord = null;
    }
  }
  if (historyRecord === null) {
    return failure(run, "bad_engine_output", "improvement exited 0 without a new history record");
  }

  // The improvement CLI does not print its decision, so ask the engine's own
  // promotion rule about the record it just wrote.
  const historyPath = path.join(historyDir, historyFile ?? "");
  const check = await deps.run({
    module: "odte_spy_ibkr.hosted_state",
    args: ["--promotion-check", historyPath],
    timeoutMs: 60_000,
  });
  const decision = check.exitCode === 0 ? parseJsonObject(check.stdout) : null;
  const decisionReason = typeof decision?.reason === "string" ? decision.reason : null;
  return {
    ok: true as const,
    historyFile: historyPath,
    historyRecord,
    decision,
    decisionReason,
    // The engine reaches this reason only after the challenger has cleared the
    // Sharpe target and the margin over the active params. null: not known.
    qualifiedForPaperPromotion:
      decisionReason === null ? null : decisionReason === "paper_auto_promote_disabled",
    activeParamsChanged: (await readIfExists(activePath)) !== activeBefore,
    chainCoverage: coverage,
  };
}

export async function signalState(deps = defaultDeps()) {
  const run = await deps.run({
    module: "odte_spy_ibkr.hosted_state",
    args: [],
    timeoutMs: 2 * 60_000,
  });
  if (run.exitCode !== 0) {
    return failureOf(run);
  }
  try {
    return { ok: true as const, state: JSON.parse(run.stdout) as Record<string, Json> };
  } catch {
    return failure(run, "bad_engine_output", "the state module did not print JSON");
  }
}
