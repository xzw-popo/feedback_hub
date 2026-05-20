import dayjs from 'dayjs'

export function formatTs(ms: number | null | undefined): string {
  if (ms == null) return ''
  return dayjs(ms).format('YYYY-MM-DD HH:mm')
}

export function formatTsShort(ms: number | null | undefined): string {
  if (ms == null) return ''
  return dayjs(ms).format('MM-DD HH:mm')
}

export function truncate(s: string | null | undefined, max: number): string {
  if (!s) return ''
  if (s.length <= max) return s
  return s.slice(0, max) + '…'
}

export function formatPercent(ratio: number): string {
  if (Number.isNaN(ratio) || !Number.isFinite(ratio)) return '—'
  return `${(ratio * 100).toFixed(1)}%`
}
