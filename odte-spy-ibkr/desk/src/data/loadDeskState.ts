import type { DeskState } from './types'
import { mockDeskState } from './mockFallback'
import { parseDeskState } from './parseDeskState'

/** Same-origin proxy → desk_api (avoids CORS). Fallback to direct URL. */
const ENGINE_URLS = [
  '/api/desk/state',
  import.meta.env.VITE_DESK_STATE_URL?.trim() || 'http://127.0.0.1:8787/desk/state',
]

/**
 * Prefer live desk_api / signal_state. Static mock is fallback only.
 * Desk UI never places orders; engine safety flags are shown truthfully.
 * Malformed JSON keeps the previous usable state.
 */
export async function loadDeskState(prior: DeskState = mockDeskState): Promise<DeskState> {
  let state: DeskState = prior

  for (const url of ENGINE_URLS) {
    try {
      const res = await fetch(url, {
        cache: 'no-store',
        signal: AbortSignal.timeout(2500),
      })
      if (!res.ok) continue
      const parsed = parseDeskState(await res.json(), state)
      if (parsed) {
        return finalizeDeskState({ ...parsed, source: 'engine' })
      }
    } catch {
      // try next URL
    }
  }

  // Optional file mirror via Vite public / or proxied path
  try {
    const res = await fetch('/api/desk/signal_state.json', {
      cache: 'no-store',
      signal: AbortSignal.timeout(1500),
    })
    if (res.ok) {
      const parsed = parseDeskState(await res.json(), state)
      if (parsed) {
        return finalizeDeskState({ ...parsed, source: 'file' })
      }
    }
  } catch {
    // continue
  }

  try {
    const res = await fetch('/mock/desk-state.json', { cache: 'no-store' })
    if (res.ok) {
      const parsed = parseDeskState(await res.json(), state)
      if (parsed) {
        return finalizeDeskState({ ...parsed, source: 'static' })
      }
    }
  } catch {
    // keep prior / mock
  }

  return finalizeDeskState(state.source === 'mock' ? state : { ...state, source: state.source })
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
