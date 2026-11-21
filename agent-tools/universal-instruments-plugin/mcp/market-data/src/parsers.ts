export interface ParsedOccOption {
  root: string;
  expiration: string; // YYYY-MM-DD
  type: "call" | "put";
  strike: number;
}

export interface CboeRawOption {
  option: string;
  bid?: number | null;
  ask?: number | null;
  last_trade_price?: number | null;
  iv?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  volume?: number | null;
  open_interest?: number | null;
}

export interface CboeOptionContract {
  option: string;
  root: string;
  expiration: string;
  type: "call" | "put";
  strike: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  last: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  volume: number;
  openInterest: number;
}

export interface FredObservation {
  date: string;
  value: number;
}

const OCC_STANDARD_RE = /^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;
const USER_AGENT_EMAIL_RE = /\S+@\S+\.\S+/;
const XSLT_PREFIX_RE = /^xsl[^/]*\//i;

export const CBOE_BAD_ETF_HOLIDAY_DATES = new Set(["2018-11-22", "2025-05-26"]);

export function toFiniteNumber(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === "boolean") {
    return null;
  }
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function roundTo(value: number, decimals = 6): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

export function parseStandardOccSymbol(optionCode: string): ParsedOccOption | null {
  const match = OCC_STANDARD_RE.exec(optionCode.trim().toUpperCase());
  if (!match) {
    return null;
  }
  const [, root, yy, mm, dd, cp, strikeRaw] = match;
  return {
    root,
    expiration: `20${yy}-${mm}-${dd}`,
    type: cp === "C" ? "call" : "put",
    strike: Number(strikeRaw) / 1000,
  };
}

function optionMid(bid: number | null, ask: number | null, last: number | null): number | null {
  if (bid !== null && ask !== null && bid >= 0 && ask > 0) {
    return roundTo((bid + ask) / 2, 4);
  }
  if (last !== null && last > 0) {
    return roundTo(last, 4);
  }
  return null;
}

export function analyzeCboeChain(
  symbol: string,
  spot: number | null,
  iv30: number | null,
  rawOptions: CboeRawOption[],
  options: {
    expiration?: string;
    strikesAroundAtm?: number;
    todayIso?: string;
  } = {}
) {
  const todayIso = options.todayIso ?? new Date().toISOString().slice(0, 10);
  const strikesAroundAtm = options.strikesAroundAtm ?? 5;

  const parsed: CboeOptionContract[] = [];
  let totalCallVolume = 0;
  let totalPutVolume = 0;
  let totalCallOi = 0;
  let totalPutOi = 0;
  const expirySet = new Set<string>();

  for (const raw of rawOptions) {
    if (!raw || typeof raw.option !== "string") continue;
    const occ = parseStandardOccSymbol(raw.option);
    if (!occ) continue;

    const bid = toFiniteNumber(raw.bid);
    const ask = toFiniteNumber(raw.ask);
    const last = toFiniteNumber(raw.last_trade_price);
    const volume = Math.max(0, toFiniteNumber(raw.volume) ?? 0);
    const openInterest = Math.max(0, toFiniteNumber(raw.open_interest) ?? 0);

    if (occ.type === "call") {
      totalCallVolume += volume;
      totalCallOi += openInterest;
    } else {
      totalPutVolume += volume;
      totalPutOi += openInterest;
    }
    expirySet.add(occ.expiration);

    parsed.push({
      option: raw.option,
      root: occ.root,
      expiration: occ.expiration,
      type: occ.type,
      strike: occ.strike,
      bid,
      ask,
      mid: optionMid(bid, ask, last),
      last,
      iv: toFiniteNumber(raw.iv),
      delta: toFiniteNumber(raw.delta),
      gamma: toFiniteNumber(raw.gamma),
      theta: toFiniteNumber(raw.theta),
      vega: toFiniteNumber(raw.vega),
      volume,
      openInterest,
    });
  }

  const allExpirations = Array.from(expirySet).sort();
  const upcomingExpirations = allExpirations.filter((d) => d >= todayIso);

  let selectedExpiration: string | null = null;
  if (options.expiration) {
    if (!expirySet.has(options.expiration)) {
      throw new Error(
        `Expiration ${options.expiration} not found for ${symbol}. Available upcoming expirations: ${upcomingExpirations.slice(0, 10).join(", ") || "none"}`
      );
    }
    selectedExpiration = options.expiration;
  } else {
    selectedExpiration = upcomingExpirations[0] ?? allExpirations[allExpirations.length - 1] ?? null;
  }

  let atmStraddle: {
    expiration: string;
    strike: number;
    callMid: number | null;
    putMid: number | null;
    straddlePrice: number | null;
    impliedMovePct: number | null;
  } | null = null;

  let atmSlice: { calls: CboeOptionContract[]; puts: CboeOptionContract[] } = {
    calls: [],
    puts: [],
  };

  if (selectedExpiration) {
    const expContracts = parsed.filter((c) => c.expiration === selectedExpiration);
    const strikes = Array.from(new Set(expContracts.map((c) => c.strike))).sort((a, b) => a - b);
    const refSpot =
      spot && spot > 0
        ? spot
        : strikes.length > 0
          ? strikes[Math.floor(strikes.length / 2)]
          : null;

    if (refSpot !== null && strikes.length > 0) {
      let atmIndex = 0;
      let bestDist = Infinity;
      for (let i = 0; i < strikes.length; i++) {
        const dist = Math.abs(strikes[i] - refSpot);
        if (dist < bestDist) {
          bestDist = dist;
          atmIndex = i;
        }
      }
      const atmStrike = strikes[atmIndex];
      const baseRoot = symbol.replace(/^_/, "").toUpperCase();
      const atmCalls = expContracts.filter((c) => c.strike === atmStrike && c.type === "call");
      const atmPuts = expContracts.filter((c) => c.strike === atmStrike && c.type === "put");
      // Keep ATM call and put on the same OCC root (e.g. do not mix AM-settled SPX with PM-settled SPXW).
      const atmCall = atmCalls.find((c) => c.root === baseRoot) ?? atmCalls[0];
      const atmPut =
        (atmCall ? atmPuts.find((p) => p.root === atmCall.root) : undefined) ??
        atmPuts.find((p) => p.root === baseRoot) ??
        atmPuts[0];
      const callMid = atmCall?.mid ?? null;
      const putMid = atmPut?.mid ?? null;
      const straddlePrice =
        callMid !== null && putMid !== null ? roundTo(callMid + putMid, 4) : null;
      const impliedMovePct =
        straddlePrice !== null && spot !== null && spot > 0
          ? roundTo((straddlePrice / spot) * 100, 2)
          : null;

      atmStraddle = {
        expiration: selectedExpiration,
        strike: atmStrike,
        callMid,
        putMid,
        straddlePrice,
        impliedMovePct,
      };

      const start = Math.max(0, atmIndex - strikesAroundAtm);
      const end = Math.min(strikes.length, atmIndex + strikesAroundAtm + 1);
      const keepStrikes = new Set(strikes.slice(start, end));
      atmSlice = {
        calls: expContracts
          .filter((c) => c.type === "call" && keepStrikes.has(c.strike))
          .sort((a, b) => a.strike - b.strike),
        puts: expContracts
          .filter((c) => c.type === "put" && keepStrikes.has(c.strike))
          .sort((a, b) => a.strike - b.strike),
      };
    }
  }

  return {
    symbol,
    spot,
    iv30,
    standardContractCount: parsed.length,
    totals: {
      callVolume: totalCallVolume,
      putVolume: totalPutVolume,
      putCallVolumeRatio:
        totalCallVolume > 0 ? roundTo(totalPutVolume / totalCallVolume, 4) : null,
      callOpenInterest: totalCallOi,
      putOpenInterest: totalPutOi,
      putCallOpenInterestRatio: totalCallOi > 0 ? roundTo(totalPutOi / totalCallOi, 4) : null,
    },
    selectedExpiration,
    upcomingExpirations: upcomingExpirations.slice(0, 12),
    atmStraddle,
    atmSlice,
  };
}

export function parseFredCsv(csvText: string): FredObservation[] {
  const lines = csvText.trim().split(/\r?\n/);
  const rows: FredObservation[] = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const comma = line.indexOf(",");
    if (comma === -1) continue;
    const date = line.slice(0, comma).trim();
    const rawVal = line.slice(comma + 1).trim();
    if (!date || !rawVal || rawVal === ".") continue;
    const value = Number(rawVal);
    if (!Number.isFinite(value)) continue;
    rows.push({ date, value });
  }
  return rows;
}

export function recentMondaysUtc(count = 8, now: Date = new Date()): string[] {
  const base = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const day = base.getUTCDay();
  // Shift back to Monday of the current week.
  base.setUTCDate(base.getUTCDate() - ((day + 6) % 7));
  const mondays: string[] = [];
  for (let w = 1; w <= count; w++) {
    const d = new Date(base.getTime() - w * 7 * 86_400_000);
    mondays.push(d.toISOString().slice(0, 10));
  }
  return mondays;
}

export function sanitizeSocrataSearch(term: string): string {
  return term
    .trim()
    .toUpperCase()
    .replace(/[%_\\]/g, "")
    .replace(/'/g, "''");
}

export function requireSecUserAgent(value: string | undefined): string {
  const ua = (value ?? "").trim();
  if (!ua || !USER_AGENT_EMAIL_RE.test(ua)) {
    throw new Error(
      "SEC_USER_AGENT must be set to a real contact in the form 'Name email@domain' " +
        "(for example: export SEC_USER_AGENT='Jane Doe jane@example.com')."
    );
  }
  return ua;
}

export function padCik(cik: string | number): string {
  const digits = String(cik).replace(/\D/g, "");
  if (!digits) {
    throw new Error(`Invalid CIK: ${String(cik)}`);
  }
  return digits.padStart(10, "0");
}

export function stripXsltPrefix(documentName: string): string {
  return documentName.trim().replace(/^\/+/, "").replace(XSLT_PREFIX_RE, "");
}
