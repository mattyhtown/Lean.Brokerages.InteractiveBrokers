import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SECRET_NAMES } from "./hosting.js";

const LIB_DIR = path.dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = path.resolve(LIB_DIR, "..", "..");
export const SAFETY_YAML_PATH = path.join(LIB_DIR, "engine-safety.yaml");

// The live runner (`python -m odte_spy_ibkr`) is deliberately absent: it is
// the only engine entry point that can reach `place_combo_order`.
export const ENGINE_MODULES = [
  "odte_spy_ibkr.research",
  "odte_spy_ibkr.backtest",
  "odte_spy_ibkr.improvement",
  "odte_spy_ibkr.hosted_state",
] as const;

export type EngineModule = (typeof ENGINE_MODULES)[number];

const PASSTHROUGH_ENV = [
  "PATH",
  "HOME",
  "LANG",
  "LC_ALL",
  "TZ",
  "TMPDIR",
  "PYTHONPATH",
  "VIRTUAL_ENV",
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "NO_PROXY",
  "http_proxy",
  "https_proxy",
  "no_proxy",
  "SSL_CERT_FILE",
  "SSL_CERT_DIR",
  "REQUESTS_CA_BUNDLE",
];

export const FORCED_ENV: Record<string, string> = {
  LIVE_TRADING: "false",
  READ_ONLY: "true",
  ENABLE_ORDERS: "false",
  PAPER: "true",
  IMPROVEMENT_AUTO_PROMOTE_LIVE: "false",
  // A promotion would rewrite params/active.yaml on a disk the hosting
  // platform can replace at any time, so the hosted run only reports.
  IMPROVEMENT_AUTO_PROMOTE_PAPER: "false",
  // `lean data download --yes` buys files with the organization's QCC balance.
  QUANTCONNECT_ALLOW_PAID_DOWNLOAD: "false",
  CONFIG_YAML: SAFETY_YAML_PATH,
  PYTHONUNBUFFERED: "1",
};

/**
 * Environment for an engine process: an allowlist, never the host environment.
 * Nothing the caller or the host sets can change a FORCED_ENV value.
 */
export function buildEngineEnv(hostEnv: NodeJS.ProcessEnv): Record<string, string> {
  const env: Record<string, string> = {};
  for (const name of [...PASSTHROUGH_ENV, ...SECRET_NAMES]) {
    const value = hostEnv[name];
    if (value !== undefined && value !== "") {
      env[name] = value;
    }
  }
  return { ...env, ...FORCED_ENV };
}

export function resolveEngineDir(hostEnv: NodeJS.ProcessEnv): string {
  return hostEnv.ODTE_ENGINE_DIR || path.resolve(AGENT_DIR, "..");
}

export function resolvePython(hostEnv: NodeJS.ProcessEnv): string {
  return hostEnv.ODTE_PYTHON || "python3";
}

/** Replaces every secret value found in `text`, raw or URL-encoded. */
export function redactSecrets(text: string, hostEnv: NodeJS.ProcessEnv): string {
  let out = text;
  for (const name of SECRET_NAMES) {
    const value = hostEnv[name];
    if (value === undefined || value.length < 4) {
      continue;
    }
    for (const needle of new Set([value, encodeURIComponent(value)])) {
      out = out.split(needle).join(`[REDACTED:${name}]`);
    }
  }
  return out;
}

export type EngineRequest = {
  module: EngineModule;
  args: string[];
  timeoutMs: number;
};

export type EngineRun = {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  spawnError: string | null;
};

export type EngineRunner = (request: EngineRequest) => Promise<EngineRun>;

const MAX_CAPTURE_CHARS = 2_000_000;

function appendCapped(buffer: string, chunk: string): string {
  const next = buffer + chunk;
  return next.length > MAX_CAPTURE_CHARS ? next.slice(next.length - MAX_CAPTURE_CHARS) : next;
}

export function createEngineRunner(hostEnv: NodeJS.ProcessEnv = process.env): EngineRunner {
  return ({ module, args, timeoutMs }) =>
    new Promise((resolve) => {
      if (!ENGINE_MODULES.includes(module)) {
        resolve({
          exitCode: null,
          stdout: "",
          stderr: "",
          timedOut: false,
          spawnError: `module not allowed: ${String(module)}`,
        });
        return;
      }
      const child = spawn(resolvePython(hostEnv), ["-m", module, ...args], {
        cwd: resolveEngineDir(hostEnv),
        env: buildEngineEnv(hostEnv),
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
      });
      let stdout = "";
      let stderr = "";
      let timedOut = false;
      let settled = false;
      const timer = setTimeout(() => {
        timedOut = true;
        child.kill("SIGKILL");
      }, timeoutMs);
      const finish = (exitCode: number | null, spawnError: string | null) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        resolve({
          exitCode,
          stdout: redactSecrets(stdout, hostEnv),
          stderr: redactSecrets(stderr, hostEnv),
          timedOut,
          spawnError,
        });
      };
      child.stdout.setEncoding("utf8").on("data", (chunk: string) => {
        stdout = appendCapped(stdout, chunk);
      });
      child.stderr.setEncoding("utf8").on("data", (chunk: string) => {
        stderr = appendCapped(stderr, chunk);
      });
      child.on("error", (error) => finish(null, error.message));
      child.on("close", (code) => finish(code, null));
    });
}
