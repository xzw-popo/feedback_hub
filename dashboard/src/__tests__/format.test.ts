import { describe, it, expect } from 'vitest'
import { formatTs, formatTsShort, truncate, formatPercent } from '@/utils/format'

describe('formatTs', () => {
  it('formats unix ms to YYYY-MM-DD HH:mm', () => {
    const ms = new Date(2026, 4, 21, 14, 30, 0).getTime()
    expect(formatTs(ms)).toBe('2026-05-21 14:30')
  })
  it('returns empty string for null', () => {
    expect(formatTs(null)).toBe('')
  })
})

describe('formatTsShort', () => {
  it('formats unix ms to MM-DD HH:mm', () => {
    const ms = new Date(2026, 4, 21, 14, 30, 0).getTime()
    expect(formatTsShort(ms)).toBe('05-21 14:30')
  })
})

describe('truncate', () => {
  it('returns full text when shorter than limit', () => {
    expect(truncate('hello', 10)).toBe('hello')
  })
  it('truncates and appends ellipsis', () => {
    expect(truncate('1234567890abcdef', 10)).toBe('1234567890…')
  })
  it('handles null/undefined', () => {
    expect(truncate(null, 10)).toBe('')
    expect(truncate(undefined, 10)).toBe('')
  })
})

describe('formatPercent', () => {
  it('formats 0.476 to "47.6%"', () => {
    expect(formatPercent(0.476)).toBe('47.6%')
  })
  it('formats 0 to "0.0%"', () => {
    expect(formatPercent(0)).toBe('0.0%')
  })
  it('handles NaN', () => {
    expect(formatPercent(Number.NaN)).toBe('—')
  })
})
