import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  formatDateTime,
  formatTime,
  isSameAthensDay,
  isOnAthensCalendarDay,
  moveToAthensDay,
  athensDateTimeLocalToIso,
  toAthensDateTimeLocal,
  isSafeImageUrl,
  isSafeVideoUrl,
  generateId,
  debounce,
} from './utils'

describe('formatDateTime / formatTime', () => {
  it('formats in Europe/Athens wall clock', () => {
    // 2024-06-01T12:00:00Z is 15:00 Athens (UTC+3, DST)
    expect(formatDateTime('2024-06-01T12:00:00Z')).toContain('2024')
    expect(formatTime('2024-06-01T12:00:00Z')).toBe('15:00')
  })

  it('honours option overrides', () => {
    const out = formatDateTime('2024-06-01T12:00:00Z', { month: 'long' })
    expect(out).toContain('June')
  })
})

describe('Athens calendar helpers', () => {
  it('isSameAthensDay distinguishes crossing midnight', () => {
    expect(isSameAthensDay('2024-06-01T22:30:00Z', '2024-06-02T00:30:00Z')).toBe(true) // both Jun 2 Athens
    expect(isSameAthensDay('2024-06-01T20:00:00Z', '2024-06-02T12:00:00Z')).toBe(false)
  })

  it('isOnAthensCalendarDay matches a Date cell', () => {
    const day = new Date(2024, 5, 2) // Jun 2 local cell
    expect(isOnAthensCalendarDay('2024-06-01T22:30:00Z', day)).toBe(true)
    expect(isOnAthensCalendarDay('2024-06-01T10:00:00Z', day)).toBe(false)
  })

  it('moveToAthensDay keeps the Athens wall-clock time on a new day', () => {
    const moved = moveToAthensDay('2024-06-01T09:15:00Z', new Date(2024, 5, 10)) // Jun 10 cell
    expect(toAthensDateTimeLocal(moved).endsWith('12:15')).toBe(true)
    expect(toAthensDateTimeLocal(moved).startsWith('2024-06-10')).toBe(true)
  })

  it('athensDateTimeLocalToIso round-trips and handles empty input', () => {
    expect(athensDateTimeLocalToIso('')).toBe('')
    const iso = athensDateTimeLocalToIso('2024-06-10T12:15')
    expect(toAthensDateTimeLocal(iso)).toBe('2024-06-10T12:15')
    // also exercises the no-T default-time branch
    expect(() => athensDateTimeLocalToIso('2024-06-10')).not.toThrow()
  })
})

describe('safe media URL validators', () => {
  it('accepts http(s), data and blob URLs for images', () => {
    expect(isSafeImageUrl('https://cdn.x/a.png')).toBe(true)
    expect(isSafeImageUrl('http://x/a.png')).toBe(true)
    expect(isSafeImageUrl('data:image/png;base64,xx')).toBe(true)
    expect(isSafeImageUrl('blob:http://localhost/abc')).toBe(true)
  })

  it('rejects javascript: and other schemes', () => {
    expect(isSafeImageUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeImageUrl('file:///etc/passwd')).toBe(false)
    expect(isSafeVideoUrl('javascript:alert(1)')).toBe(false)
  })

  it('rejects nullish and unparseable values', () => {
    expect(isSafeImageUrl(null)).toBe(false)
    expect(isSafeImageUrl(undefined)).toBe(false)
    expect(isSafeVideoUrl('')).toBe(false)
    // Force the URL constructor failure branch (invalid port throws)
    expect(isSafeVideoUrl('http://x:abc/')).toBe(false)
    expect(isSafeImageUrl('http://x:99999/')).toBe(false)
  })

  it('accepts blob/data for video', () => {
    expect(isSafeVideoUrl('blob:http://localhost/abc')).toBe(true)
    expect(isSafeVideoUrl('data:video/mp4;base64,xx')).toBe(true)
  })
})

describe('generateId / debounce', () => {
  it('generateId returns a 13-char hex id', () => {
    const id = generateId()
    expect(id).toMatch(/^[0-9a-f]{13}$/)
    expect(generateId()).not.toBe(id)
  })

  it('debounce coalesces calls within the wait window', () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const debounced = debounce(fn, 100)
    debounced('a')
    debounced('b')
    debounced('c')
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(150)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('c')
    vi.useRealTimers()
  })
})
