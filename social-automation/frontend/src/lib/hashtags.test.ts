import { describe, it, expect } from 'vitest'
import { normalizeTag, normalizeTagList } from './hashtags'

describe('normalizeTag', () => {
  it('strips a single leading #', () => {
    expect(normalizeTag('#cloudless')).toBe('cloudless')
  })

  it('strips repeated leading #', () => {
    expect(normalizeTag('##cloudless')).toBe('cloudless')
    expect(normalizeTag('###tag')).toBe('tag')
  })

  it('trims whitespace around the tag', () => {
    expect(normalizeTag('  #tag  ')).toBe('tag')
  })

  it('leaves bare tags untouched', () => {
    expect(normalizeTag('cloudless')).toBe('cloudless')
  })
})

describe('normalizeTagList', () => {
  it('strips # prefixes from every tag', () => {
    expect(normalizeTagList(['#cloudless', 'serverless', '##aws'])).toEqual([
      'cloudless', 'serverless', 'aws',
    ])
  })

  it('dedupes case-insensitively, keeping first occurrence', () => {
    expect(normalizeTagList(['Cloud', '#cloud', '#CLOUD'])).toEqual(['Cloud'])
  })

  it('drops empty and hash-only entries', () => {
    expect(normalizeTagList(['#', '', '  ', 'ok'])).toEqual(['ok'])
  })

  it('handles an empty list', () => {
    expect(normalizeTagList([])).toEqual([])
  })
})
