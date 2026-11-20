export const money = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || !Number.isFinite(value) ? '—' :
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)

export const signedMoney = (value: number | null | undefined) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${money(value)}`
}

export const number = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || !Number.isFinite(value) ? '—' :
  new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)

export const percent = (value: number | null | undefined, digits = 1) =>
  value === null || value === undefined || !Number.isFinite(value) ? '—' : `${value.toFixed(digits)}%`

export const strategyName = (value: string) => ({
  momentum_breakout: 'Momentum breakout',
  trend_continuation: 'Trend continuation',
  vwap_reversal: 'VWAP reversal',
  recovered_broker_position: 'Recovered position',
}[value] || value.replaceAll('_', ' '))

export const formatTime = (value: string | null | undefined, withDate = false) => {
  if (!value) return '—'
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', month: withDate ? 'short' : undefined, day: withDate ? 'numeric' : undefined,
    hour: '2-digit', minute: '2-digit', second: withDate ? undefined : '2-digit', hour12: false,
  }).format(new Date(value))
}

export function downloadFile(name: string, content: string, type = 'text/plain') {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}
