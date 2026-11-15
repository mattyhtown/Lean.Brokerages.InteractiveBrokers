export type GateStatus = 'pass' | 'watch' | 'block'

export interface GateState {
  id: string
  label: string
  status: GateStatus
  value: number
  threshold: number
  detail: string
}

export interface SafetyState {
  liveTrading: boolean
  readOnly: boolean
  enableOrders: boolean
  ordersLockedReason: string
  killSwitchArmed: boolean
  killSwitchLabel: string
  /** Desk chrome always treats this as true — UI cannot place orders */
  uiOrdersLocked?: boolean
}

export interface FlattenState {
  flattenTimeEt: string
  entryCutoffEt: string
  shouldFlatten: boolean
  minutesToFlatten: number
  rth: boolean
}

export interface PositionRow {
  id: string
  side: string
  contracts: number
  entryCredit: number
  openedAt: string
  manageAction: 'hold' | 'flatten' | 'stop'
}

export interface SignalRow {
  id: string
  side: string
  summary: string
  strength: number
  blocked: boolean
  reason: string
}

export interface ChainSkewState {
  netCallBias: number
  callPremiumMid: number
  putPremiumMid: number
  callVolume: number
  putVolume: number
  note: string
}

export interface MacroHeadline {
  source: string
  title: string
  score: number
}

export interface MacroState {
  riskOff: boolean
  headlineIntensity: number
  blackout: boolean
  headlines: MacroHeadline[]
}

export interface BacktestState {
  windowDays: number
  trades: number
  winRate: number
  rollingSharpe: number
  maxDrawdown: number
  avgCredit: number
  note: string
}

export interface StationMeta {
  id: string
  title: string
  subtitle: string
}

export interface DeskState {
  asOf: string
  brand: string
  symbol: string
  mode: string
  safety: SafetyState
  spot: number
  gates: Record<string, GateState>
  flatten: FlattenState
  positions: PositionRow[]
  signals: SignalRow[]
  chainSkew: ChainSkewState
  macro: MacroState
  backtest: BacktestState
  stations: StationMeta[]
  source: 'mock' | 'engine' | 'static' | 'file'
  degraded?: string[]
  tick?: number
}
