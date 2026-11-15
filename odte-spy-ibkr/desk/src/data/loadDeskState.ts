import type { DeskState } from './types'
import { mockDeskState } from './mockFallback'

const ENGINE_URL =
  import.meta.env.VITE_DESK_STATE_URL?.trim() || 'http://127.0.0.1:8787/desk/state'

/**
 * Prefer static mock JSON shipped with the app.
 * Optionally attempt a local engine endpoint — never requires IBKR.
 * ENABLE_ORDERS is forced false regardless of payload.
 */
export async function loadDeskState(): Promise<DeskState> {
  let state: DeskState = mockDeskState

  try {
    const res = await fetch('/mock/desk-state.json', { cache: 'no-store' })
    if (res.ok) {
      state = (await res.json()) as DeskState
      state.source = 'static'
    }
  } catch {
    // keep embedded mock
  }

  if (import.meta.env.VITE_DESK_TRY_ENGINE === '1') {
    try {
      const res = await fetch(ENGINE_URL, {
        cache: 'no-store',
        signal: AbortSignal.timeout(1500),
      })
      if (res.ok) {
        state = (await res.json()) as DeskState
        state.source = 'engine'
      }
    } catch {
      // optional — stay on mock/static
    }
  }

  return lockOrders(state)
}

function lockOrders(state: DeskState): DeskState {
  return {
    ...state,
    safety: {
      ...state.safety,
      enableOrders: false,
      readOnly: true,
      ordersLockedReason:
        state.safety.ordersLockedReason ||
        'ENABLE_ORDERS is false — UI cannot arm live placement',
    },
  }
}
