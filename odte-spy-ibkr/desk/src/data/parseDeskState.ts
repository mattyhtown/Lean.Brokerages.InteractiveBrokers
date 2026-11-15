import type {
  BacktestState,
  ChainSkewState,
  DeskState,
  FlattenState,
  GateState,
  GateStatus,
  MacroState,
  PositionRow,
  SafetyState,
  SignalRow,
  StationMeta,
} from './types'
import { mockDeskState } from './mockFallback'

const GATE_STATUSES: readonly GateStatus[] = ['pass', 'watch', 'block']

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function asBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function parseGate(raw: unknown, fallback: GateState): GateState {
  if (!isRecord(raw)) return fallback
  const status = asString(raw.status, fallback.status)
  return {
    id: asString(raw.id, fallback.id),
    label: asString(raw.label, fallback.label),
    status: (GATE_STATUSES.includes(status as GateStatus)
      ? status
      : fallback.status) as GateStatus,
    value: asNumber(raw.value, fallback.value),
    threshold: asNumber(raw.threshold, fallback.threshold),
    detail: asString(raw.detail, fallback.detail),
  }
}

function parseGates(
  raw: unknown,
  fallback: DeskState['gates'],
): DeskState['gates'] {
  const src = isRecord(raw) ? raw : {}
  return {
    sharpe: parseGate(src.sharpe, fallback.sharpe),
    macro: parseGate(src.macro, fallback.macro),
    flow: parseGate(src.flow, fallback.flow),
    risk: parseGate(src.risk, fallback.risk),
  }
}

function parseSafety(raw: unknown, fallback: SafetyState): SafetyState {
  if (!isRecord(raw)) return fallback
  return {
    liveTrading: asBoolean(raw.liveTrading, fallback.liveTrading),
    readOnly: asBoolean(raw.readOnly, fallback.readOnly),
    enableOrders: asBoolean(raw.enableOrders, fallback.enableOrders),
    ordersLockedReason: asString(raw.ordersLockedReason, fallback.ordersLockedReason),
    killSwitchArmed: asBoolean(raw.killSwitchArmed, fallback.killSwitchArmed),
    killSwitchLabel: asString(raw.killSwitchLabel, fallback.killSwitchLabel),
    uiOrdersLocked: asBoolean(raw.uiOrdersLocked, true),
  }
}

function parseFlatten(raw: unknown, fallback: FlattenState): FlattenState {
  if (!isRecord(raw)) return fallback
  return {
    flattenTimeEt: asString(raw.flattenTimeEt, fallback.flattenTimeEt),
    entryCutoffEt: asString(raw.entryCutoffEt, fallback.entryCutoffEt),
    shouldFlatten: asBoolean(raw.shouldFlatten, fallback.shouldFlatten),
    minutesToFlatten: asNumber(raw.minutesToFlatten, fallback.minutesToFlatten),
    rth: asBoolean(raw.rth, fallback.rth),
  }
}

function parseSignals(raw: unknown, fallback: SignalRow[]): SignalRow[] {
  if (!Array.isArray(raw)) return fallback
  const out: SignalRow[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    out.push({
      id: asString(item.id, `sig-${out.length}`),
      side: asString(item.side, 'unknown'),
      summary: asString(item.summary, ''),
      strength: asNumber(item.strength, 0),
      blocked: asBoolean(item.blocked, true),
      reason: asString(item.reason, ''),
    })
  }
  return out
}

function parsePositions(raw: unknown, fallback: PositionRow[]): PositionRow[] {
  if (!Array.isArray(raw)) return fallback
  const out: PositionRow[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const action = asString(item.manageAction, 'hold')
    out.push({
      id: asString(item.id, `pos-${out.length}`),
      side: asString(item.side, 'unknown'),
      contracts: asNumber(item.contracts, 0),
      entryCredit: asNumber(item.entryCredit, 0),
      openedAt: asString(item.openedAt, ''),
      manageAction:
        action === 'flatten' || action === 'stop' || action === 'hold'
          ? action
          : 'hold',
    })
  }
  return out
}

function parseChainSkew(raw: unknown, fallback: ChainSkewState): ChainSkewState {
  if (!isRecord(raw)) return fallback
  return {
    netCallBias: asNumber(raw.netCallBias, fallback.netCallBias),
    callPremiumMid: asNumber(raw.callPremiumMid, fallback.callPremiumMid),
    putPremiumMid: asNumber(raw.putPremiumMid, fallback.putPremiumMid),
    callVolume: asNumber(raw.callVolume, fallback.callVolume),
    putVolume: asNumber(raw.putVolume, fallback.putVolume),
    note: asString(raw.note, fallback.note),
  }
}

function parseMacro(raw: unknown, fallback: MacroState): MacroState {
  if (!isRecord(raw)) return fallback
  const headlines = Array.isArray(raw.headlines)
    ? raw.headlines
        .filter(isRecord)
        .map((h) => ({
          source: asString(h.source, 'unknown'),
          title: asString(h.title, ''),
          score: asNumber(h.score, 0),
        }))
    : fallback.headlines
  return {
    riskOff: asBoolean(raw.riskOff, fallback.riskOff),
    headlineIntensity: asNumber(raw.headlineIntensity, fallback.headlineIntensity),
    blackout: asBoolean(raw.blackout, fallback.blackout),
    headlines,
  }
}

function parseBacktest(raw: unknown, fallback: BacktestState): BacktestState {
  if (!isRecord(raw)) return fallback
  return {
    windowDays: asNumber(raw.windowDays, fallback.windowDays),
    trades: asNumber(raw.trades, fallback.trades),
    winRate: asNumber(raw.winRate, fallback.winRate),
    rollingSharpe: asNumber(raw.rollingSharpe, fallback.rollingSharpe),
    maxDrawdown: asNumber(raw.maxDrawdown, fallback.maxDrawdown),
    avgCredit: asNumber(raw.avgCredit, fallback.avgCredit),
    note: asString(raw.note, fallback.note),
  }
}

function parseStations(raw: unknown, fallback: StationMeta[]): StationMeta[] {
  if (!Array.isArray(raw)) return fallback
  const out: StationMeta[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    out.push({
      id: asString(item.id, `station-${out.length}`),
      title: asString(item.title, 'Station'),
      subtitle: asString(item.subtitle, ''),
    })
  }
  return out.length > 0 ? out : fallback
}

/**
 * Validate / normalize unknown JSON into DeskState.
 * Returns null when the payload is not a usable object so callers keep prior state.
 */
export function parseDeskState(
  raw: unknown,
  fallback: DeskState = mockDeskState,
): DeskState | null {
  if (!isRecord(raw)) return null

  // Require a few identity fields so random JSON cannot replace the desk.
  if (typeof raw.brand !== 'string' && typeof raw.symbol !== 'string') {
    return null
  }

  return {
    asOf: asString(raw.asOf, fallback.asOf),
    brand: asString(raw.brand, fallback.brand),
    symbol: asString(raw.symbol, fallback.symbol),
    mode: asString(raw.mode, fallback.mode),
    safety: parseSafety(raw.safety, fallback.safety),
    spot: asNumber(raw.spot, fallback.spot),
    gates: parseGates(raw.gates, fallback.gates),
    flatten: parseFlatten(raw.flatten, fallback.flatten),
    positions: parsePositions(raw.positions, fallback.positions),
    signals: parseSignals(raw.signals, fallback.signals),
    chainSkew: parseChainSkew(raw.chainSkew, fallback.chainSkew),
    macro: parseMacro(raw.macro, fallback.macro),
    backtest: parseBacktest(raw.backtest, fallback.backtest),
    stations: parseStations(raw.stations, fallback.stations),
    source:
      raw.source === 'engine' ||
      raw.source === 'static' ||
      raw.source === 'mock' ||
      raw.source === 'file'
        ? raw.source
        : fallback.source,
    degraded: Array.isArray(raw.degraded)
      ? raw.degraded.filter((d): d is string => typeof d === 'string')
      : fallback.degraded,
    tick: asNumber(raw.tick, fallback.tick ?? 0),
  }
}
