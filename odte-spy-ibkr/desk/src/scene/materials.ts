export const palette = {
  concrete: '#2a2e2c',
  concreteLite: '#3a403c',
  steel: '#6d7570',
  scaffold: '#c4a574',
  scaffoldDark: '#8a6f45',
  copper: '#b87333',
  amber: '#e0a45a',
  mint: '#7dcea0',
  coral: '#e07a5f',
  ink: '#121614',
  fog: '#9aa89a',
  board: '#1a1f1c',
  panelGlass: '#d8e0d4',
} as const

export function statusColor(status: 'pass' | 'watch' | 'block'): string {
  if (status === 'pass') return palette.mint
  if (status === 'watch') return palette.amber
  return palette.coral
}
