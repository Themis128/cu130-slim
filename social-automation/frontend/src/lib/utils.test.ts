import { cn, formatDate, formatRelativeTime, truncate, generateId, debounce } from '@/lib/utils'

describe('lib/utils', () => {
  describe('cn', () => {
    it('merges class names', () => {
      expect(cn('base', 'extra')).toBe('base extra')
    })

    it('handles conditional classes', () => {
      expect(cn('base', true && 'conditional')).toBe('base conditional')
      expect(cn('base', false && 'conditional')).toBe('base')
    })

    it('handles tailwind merge conflicts', () => {
      expect(cn('p-2 p-4')).toBe('p-4')
      expect(cn('text-red-500', 'text-blue-500')).toBe('text-blue-500')
    })

    it('handles objects and arrays', () => {
      expect(cn({ active: true, disabled: false })).toBe('active')
      expect(cn(['a', 'b', { c: true }])).toBe('a b c')
    })
  })

  describe('formatDate', () => {
    it('formats date string', () => {
      const result = formatDate('2024-01-15')
      expect(result).toContain('Jan')
      expect(result).toContain('15')
      expect(result).toContain('2024')
    })

    it('formats Date object', () => {
      const result = formatDate(new Date('2024-01-15'))
      expect(result).toContain('Jan')
      expect(result).toContain('15')
      expect(result).toContain('2024')
    })

    it('accepts custom options', () => {
      const result = formatDate('2024-01-15', { month: 'long', year: '2-digit' })
      expect(result).toContain('January')
      expect(result).toContain('24')
    })
  })

  describe('formatRelativeTime', () => {
    it('returns "just now" for recent times', () => {
      const now = new Date()
      expect(formatRelativeTime(now)).toBe('just now')
    })

    it('returns minutes ago', () => {
      const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000)
      expect(formatRelativeTime(fiveMinutesAgo)).toBe('5m ago')
    })

    it('returns hours ago', () => {
      const threeHoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000)
      expect(formatRelativeTime(threeHoursAgo)).toBe('3h ago')
    })

    it('returns days ago', () => {
      const twoDaysAgo = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000)
      expect(formatRelativeTime(twoDaysAgo)).toBe('2d ago')
    })

    it('returns formatted date for older dates', () => {
      const tenDaysAgo = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000)
      const result = formatRelativeTime(tenDaysAgo)
      expect(result).toMatch(/\w+ \d+, \d+/)
    })
  })

  describe('truncate', () => {
    it('returns original string if shorter than length', () => {
      expect(truncate('hello', 10)).toBe('hello')
    })

    it('truncates and adds ellipsis', () => {
      expect(truncate('hello world', 8)).toBe('hello wo...')
    })

    it('handles exact length', () => {
      expect(truncate('hello', 5)).toBe('hello')
    })
  })

  describe('generateId', () => {
    it('generates a string id', () => {
      const id = generateId()
      expect(typeof id).toBe('string')
      expect(id.length).toBeGreaterThan(0)
    })

    it('generates unique ids', () => {
      const ids = new Set()
      for (let i = 0; i < 100; i++) {
        ids.add(generateId())
      }
      expect(ids.size).toBe(100)
    })
  })

  describe('debounce', () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('delays function execution', () => {
      const fn = vi.fn()
      const debounced = debounce(fn, 100)
      debounced('arg1')
      expect(fn).not.toHaveBeenCalled()
      vi.advanceTimersByTime(100)
      expect(fn).toHaveBeenCalledWith('arg1')
    })

    it('only calls last invocation', () => {
      const fn = vi.fn()
      const debounced = debounce(fn, 100)
      debounced('arg1')
      debounced('arg2')
      debounced('arg3')
      vi.advanceTimersByTime(100)
      expect(fn).toHaveBeenCalledTimes(1)
      expect(fn).toHaveBeenCalledWith('arg3')
    })
  })
})