import { describe, it, expect } from 'vitest'
import { getAxiosErrorDetail, formatErrorToast } from './humanizeError'

describe('getAxiosErrorDetail', () => {
  it('prefers detail over message over error from response data', () => {
    expect(getAxiosErrorDetail({ response: { data: { detail: 'd', message: 'm', error: 'e' } } })).toBe('d')
    expect(getAxiosErrorDetail({ response: { data: { message: 'm', error: 'e' } } })).toBe('m')
    expect(getAxiosErrorDetail({ response: { data: { error: 'e' } } })).toBe('e')
  })

  it('skips empty/whitespace strings and non-strings', () => {
    expect(getAxiosErrorDetail({ response: { data: { detail: '   ', message: 5, error: 'x' } } })).toBe('x')
    expect(getAxiosErrorDetail({ response: { data: 'not-an-object' } })).toBeUndefined()
    expect(getAxiosErrorDetail({ response: { data: null } })).toBeUndefined()
  })

  it('falls back to the axios error message', () => {
    expect(getAxiosErrorDetail({ message: 'Network Error' })).toBe('Network Error')
  })

  it('falls back to a generic object with a message property', () => {
    expect(getAxiosErrorDetail(new Error('plain failure'))).toBe('plain failure')
  })

  it('returns undefined for non-error shapes', () => {
    expect(getAxiosErrorDetail(undefined)).toBeUndefined()
    expect(getAxiosErrorDetail('string')).toBeUndefined()
    expect(getAxiosErrorDetail({ message: 42 })).toBeUndefined()
    expect(getAxiosErrorDetail({ response: {} })).toBeUndefined()
  })
})

describe('formatErrorToast', () => {
  it('returns the human message when there is no detail', () => {
    expect(formatErrorToast('Something broke')).toBe('Something broke')
    expect(formatErrorToast('Something broke', {})).toBe('Something broke')
  })

  it('does not duplicate when detail equals the human message', () => {
    const err = { response: { data: { detail: 'Something broke' } } }
    expect(formatErrorToast('Something broke', err)).toBe('Something broke')
    expect(formatErrorToast('something broke', err)).toBe('something broke')
  })

  it('appends the technical detail on a second line', () => {
    const err = { response: { data: { detail: 'ECONNREFUSED' } } }
    expect(formatErrorToast('Could not save', err)).toBe('Could not save\nDetails: ECONNREFUSED')
  })

  it('clamps very long details with an ellipsis', () => {
    const err = { response: { data: { detail: 'x'.repeat(300) } } }
    const out = formatErrorToast('Failed', err)
    const detailLine = out.split('\nDetails: ')[1]
    expect(detailLine.endsWith('…')).toBe(true)
    expect(detailLine.length).toBe(180)
  })
})
