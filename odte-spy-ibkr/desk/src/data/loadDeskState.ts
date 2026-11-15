import type { DeskState } from './types'
import { mockDeskState } from './mockFallback'
import { parseDeskState } from './parseDeskState'

/** Same-origin proxy → desk_api (avoids CORS). Fallback to direct URL. */
const ENGINE_URLS = [
  '/api/desk/state',
  import.meta.env.VITE_DESK_STATE_URL?.trim() || 'http://127.0.0.1:8787/desk/state',
]

function withDegraded(state: DeskState, tag: string): DeskState {
  const prev = state.degraded ?? []
  if (prev.includes(tag)) return state
  return { ...state, degraded: [...prev, tag].slice(-10) }
}

/**
 * Prefer live desk_api. File mirror recovers last tick without rebuilding.
 * Static mock is cold-start only — never replaces a prior engine/file snapshot on blips.
 * Malformed JSON keeps the previous usable state.
 */
export async function loadDeskState(prior: DeskState = mockDeskState): Promise<DeskState> {
  for (const url of ENGINE_URLS) {
    try {
      const res = await fetch(url, {
        cache: 'no-store',
        signal: AbortSignal.timeout(2500),
      })
      if (!res.ok) continue
      const parsed = parseDeskState(await res.json(), prior)
      if (parsed) {
        return finalizeDeskState({ ...parsed, source: 'engine' })
      }
    } catch {
      // try next URL
    }
  }

  // Last-tick file mirror served by Vite from data/desk/signal_state.json (works if API is down)
  try {
    const res = await fetch('/api/desk/signal_state.json', {
      cache: 'no-store',
      signal: AbortSignal.timeout(1500),
    })
    if (res.ok) {
      const parsed = parseDeskState(await res.json(), prior)
      if (parsed) {
        return finalizeDeskState({ ...parsed, source: 'file' })
      }
    }
  } catch {
    // continue
  }

  // Keep last usable live/file snapshot across brief API outages — do not swap in mock
  if (prior.source === 'engine' || prior.source === 'file') {
    return finalizeDeskState(withDegraded(prior, 'api_unreachable'))
  }

  // Cold start only
  try {
    const res = await fetch('/mock/desk-state.json', { cache: 'no-store' })
    if (res.ok) {
      const parsed = parseDeskState(await res.json(), prior)
      if (parsed) {
        return finalizeDeskState({ ...parsed, source: 'static' })
      }
    }
  } catch {
    // keep prior / mock
  }

  return finalizeDeskState(prior)
}

/** Strict desk chrome: mode forced; UI order lock always on; engine flags preserved. */
export function finalizeDeskState(state: DeskState): DeskState {
  const engineEnable = Boolean(state.safety?.enableOrders)
  const engineReadOnly = state.safety?.readOnly !== false
  return {
    ...state,
    mode: 'strict',
    safety: {
      ...state.safety,
      enableOrders: engineEnable,
      readOnly: engineReadOnly,
      uiOrdersLocked: true,
      ordersLockedReason:
        state.safety?.ordersLockedReason ||
        `Desk UI locked · engine ENABLE_ORDERS=${engineEnable} READ_ONLY=${engineReadOnly}`,
    },
  }
}
