import { describe, it, expect } from 'vitest'
import { pruneIdsToKnown } from '@/lib/platforms'

describe('pruneIdsToKnown', () => {
  it('removes unknown ids and keeps order', () => {
    const known = new Set(['linkedin', 'twitter'])
    const ids = ['linkedin', 'messenger', 'twitter', 'unknown']

    expect(pruneIdsToKnown(ids, known)).toEqual(['linkedin', 'twitter'])
  })

  it('returns the same array reference when nothing is pruned', () => {
    const known = new Set(['linkedin', 'twitter'])
    const ids = ['linkedin', 'twitter']

    expect(pruneIdsToKnown(ids, known)).toBe(ids)
  })
})

