/** Industrial sci-fi ops floor — graphite, copper, signal amber. No purple neon. */
export const palette = {
  void: '#0a0e0c',
  fog: '#0f1612',
  concrete: '#1c2420',
  concreteLite: '#2a332e',
  steel: '#5c6b62',
  steelBright: '#8a9a90',
  scaffold: '#c9a066',
  scaffoldDark: '#8a6a3e',
  copper: '#b87333',
  amber: '#f0a84a',
  mint: '#5fd49a',
  coral: '#ef6b4a',
  ink: '#060908',
  panelGlass: '#d7e4d8',
  board: '#121916',
  go: '#5fd49a',
  stop: '#ef6b4a',
  warn: '#f0a84a',
} as const

export function statusColor(status: 'pass' | 'watch' | 'block'): string {
  if (status === 'pass') return palette.go
  if (status === 'watch') return palette.warn
  return palette.stop
}

export function statusVerb(status: 'pass' | 'watch' | 'block'): string {
  if (status === 'pass') return 'GO'
  if (status === 'watch') return 'HOLD'
  return 'STOP'
}
