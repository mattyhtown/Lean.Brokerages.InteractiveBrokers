import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
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
  env: NodeJS.ProcessEnv;
};

export function defaultDeps(): OperationDeps {
  return {
    run: createEngineRunner(process.env),
    engineDir: resolveEngineDir(process.env),
    env: process.env,
  };
}

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

type Failure = {
  ok: false;
  error: "engine_unavailable" | "timeout" | "engine_failed" | "bad_engine_output";
  detail: string;
  exitCode: number | null;
  logTail: string[];
};

const LOG_TAIL_LINES = 40;
const MAX_REPORT_CHARS = 20_000;
const MAX_SESSIONS = 60;
const MAX_PATHS_PER_SOURCE = 5;

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

/** The research CLI prints log lines, then one indented JSON object. */
function parseTrailingJsonObject(stdout: string): Record<string, Json> | null {
  const lines = stdout.split("\n");
  const start = lines.lastIndexOf("{");
  if (start === -1) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(lines.slice(start).join("\n"));
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, Json>)
      : null;
  } catch {
    return null;
  }
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

export type IngestSource = "alpaca" | "uw" | "orats";

export type ResearchIngestInput = {
  sources: IngestSource[];
  start?: string;
  end?: string;
  days?: number;
};

export async function researchIngest(input: ResearchIngestInput, deps = defaultDeps()) {
  const args = ["ingest", ...input.sources.flatMap((source) => ["--source", source])];
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
    return failureOf(run);
  }
  const written = parseTrailingJsonObject(run.stdout);
  if (written === null) {
    return failure(run, "bad_engine_output", "ingest exited 0 without the JSON path summary");
  }
  const sources = Object.entries(written).map(([source, paths]) => {
    const files =
      typeof paths === "object" && paths !== null && !Array.isArray(paths)
        ? Object.values(paths).map(String)
        : [];
    return { source, fileCount: files.length, sampleFiles: files.slice(0, MAX_PATHS_PER_SOURCE) };
  });
  return {
    ok: true as const,
    sourcesRequested: input.sources,
    sourcesRun: sources.map((entry) => entry.source),
    sources,
    cacheDir: path.join(deps.engineDir, "data", "research"),
  };
}

export type ResearchEvaluateInput = { holdoutDays: number };

export async function researchEvaluate(input: ResearchEvaluateInput, deps = defaultDeps()) {
  return withTempDir(async (dir) => {
    const reportPath = path.join(dir, "research-report.md");
    const run = await deps.run({
      module: "odte_spy_ibkr.research",
      args: ["evaluate", "--holdout-days", String(input.holdoutDays), "--report", reportPath],
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
      holdoutDays: input.holdoutDays,
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
  return withTempDir(async (dir) => {
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
      alpacaKeysPresent: Boolean(deps.env.ALPACA_API_KEY && deps.env.ALPACA_API_SECRET),
    };
  });
}

export async function improvementRun(deps = defaultDeps()) {
  const historyDir = path.join(deps.engineDir, "params", "history");
  const activePath = path.join(deps.engineDir, "params", "active.yaml");
  const historyBefore = new Set(await listIfExists(historyDir));
  const activeBefore = await readIfExists(activePath);

  const run = await deps.run({
    module: "odte_spy_ibkr.improvement",
    args: ["run"],
    timeoutMs: 15 * 60_000,
  });
  if (run.exitCode !== 0) {
    return failureOf(run);
  }

  const newHistory = (await listIfExists(historyDir))
    .filter((name) => !historyBefore.has(name))
    .sort();
  const historyFile = newHistory.at(-1) ?? null;
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
  let decision: Record<string, Json> | null = null;
  if (check.exitCode === 0) {
    try {
      decision = JSON.parse(check.stdout) as Record<string, Json>;
    } catch {
      decision = null;
    }
  }
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
