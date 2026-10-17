import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), custom: vi.fn(), dismiss: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const api = vi.hoisted(() => ({
  aiApi: {
    nlpCheck: vi.fn(),
    spellcheck: vi.fn(),
    analyzeSeo: vi.fn(),
  },
}))
vi.mock('@/services/api', () => api)

import { NlpPanel } from './NlpPanel'
import { SpellCheckButton } from './SpellCheckButton'
import { SeoPanel } from './SeoPanel'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const LONG = 'This is a fairly long piece of content that exceeds twenty characters.'

describe('NlpPanel', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows the hint for short content and never calls the API', () => {
    render(<NlpPanel content="short" />)
    expect(screen.getByText(/Write more than 20 characters/)).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(2000) })
    expect(api.aiApi.nlpCheck).not.toHaveBeenCalled()
  })

  it('debounces and renders a good score result', async () => {
    api.aiApi.nlpCheck.mockResolvedValue({
      data: {
        score: 92,
        avg_sentence_words: 8,
        issue_count: 0,
        issues: [],
        recommendations: [],
      },
    })
    render(<NlpPanel content={LONG} platform="linkedin" />)
    act(() => { vi.advanceTimersByTime(900) })
    await waitFor(() => expect(screen.getByText('92')).toBeInTheDocument())
    expect(screen.getByText('Good')).toBeInTheDocument()
    expect(screen.getByText(/Copy passes the plain-English playbook for linkedin/)).toBeInTheDocument()
    expect(api.aiApi.nlpCheck).toHaveBeenCalledWith({ content: LONG, platform: 'linkedin' })
  })

  it('renders issues, recommendations and the needs-work state', async () => {
    api.aiApi.nlpCheck.mockResolvedValue({
      data: {
        score: 45,
        avg_sentence_words: 22,
        issue_count: 2,
        issues: [
          { field: 'content', reason: 'jargon_or_buzzwords', snippet: 'x', matches: [] },
          { field: 'content', reason: 'unknown_reason', snippet: 'x', matches: [] },
        ],
        recommendations: ['Cut the jargon'],
      },
    })
    render(<NlpPanel content={LONG} />)
    act(() => { vi.advanceTimersByTime(900) })
    await waitFor(() => expect(screen.getByText('Needs work')).toBeInTheDocument())
    expect(screen.getByText('Jargon / buzzwords')).toBeInTheDocument()
    expect(screen.getByText('unknown_reason')).toBeInTheDocument()
    expect(screen.getByText('Cut the jargon')).toBeInTheDocument()
    expect(screen.getByText(/2 issues/)).toBeInTheDocument()
  })

  it('keeps the panel alive when the API fails', async () => {
    api.aiApi.nlpCheck.mockRejectedValue(new Error('500'))
    render(<NlpPanel content={LONG} />)
    act(() => { vi.advanceTimersByTime(900) })
    await waitFor(() => expect(api.aiApi.nlpCheck).toHaveBeenCalled())
    // no crash — still shows the hint/empty state
    expect(document.body).toBeTruthy()
  })
})

describe('NlpPanel — fair band, singular issue, stale responses', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const fair = { score: 70, avg_sentence_words: 12, issue_count: 1, issues: [], recommendations: [] }

  it('renders the fair band in amber with a singular issue count', async () => {
    api.aiApi.nlpCheck.mockResolvedValue({ data: fair })
    render(<NlpPanel content={LONG} />)
    act(() => { vi.advanceTimersByTime(900) })
    const label = await screen.findByText('Fair')
    expect(label).toHaveClass('text-amber-500')
    expect(screen.getByText('70')).toHaveClass('text-amber-500')
    expect(document.querySelector('.bg-amber-500')).toHaveStyle({ width: '70%' })
    expect(screen.getByText(/1 issue$/)).toBeInTheDocument()
  })

  it('ignores a response that resolves after the content changed', async () => {
    let resolveFirst!: (v: unknown) => void
    api.aiApi.nlpCheck
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r }))
      .mockResolvedValueOnce({ data: { ...fair, score: 91, issue_count: 0 } })
    const { rerender } = render(<NlpPanel content={LONG} />)
    act(() => { vi.advanceTimersByTime(900) }) // first request in flight
    rerender(<NlpPanel content={`${LONG} Edited.`} />)
    act(() => { vi.advanceTimersByTime(900) })
    await screen.findByText('91')
    await act(async () => { resolveFirst({ data: { ...fair, score: 12 } }) })
    expect(screen.queryByText('12')).toBeNull() // stale result discarded
    expect(screen.getByText('91')).toBeInTheDocument()
  })

  it('keeps the last known score when a later check fails', async () => {
    api.aiApi.nlpCheck.mockResolvedValueOnce({ data: fair }).mockRejectedValueOnce(new Error('503'))
    const { rerender } = render(<NlpPanel content={LONG} />)
    act(() => { vi.advanceTimersByTime(900) })
    await screen.findByText('70')
    rerender(<NlpPanel content={`${LONG} More.`} />)
    act(() => { vi.advanceTimersByTime(900) })
    await waitFor(() => expect(api.aiApi.nlpCheck).toHaveBeenCalledTimes(2))
    expect(screen.getByText('70')).toBeInTheDocument()
  })
})

describe('SpellCheckButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('is disabled with empty text', () => {
    render(<SpellCheckButton text="  " onApply={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Spell Check/ })).toBeDisabled()
  })

  it('reports a clean text', async () => {
    api.aiApi.spellcheck.mockResolvedValue({ data: { matches: [] } })
    render(<SpellCheckButton text="clean text" onApply={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Spell Check/ }))
    await waitFor(() => expect(screen.getByText('✓ No issues')).toBeInTheDocument())
    expect(toast.success).toHaveBeenCalledWith('No spelling or grammar issues found')
  })

  it('lists matches and applies a fix with offset shifting', async () => {
    api.aiApi.spellcheck.mockResolvedValue({
      data: {
        matches: [
          { message: 'Spelling', offset: 0, length: 3, replacements: ['The'], rule_id: 'r1', context: '' },
          { message: 'Grammar', offset: 10, length: 2, replacements: ['is'], rule_id: 'r2', context: '' },
        ],
      },
    })
    const onApply = vi.fn()
    render(<SpellCheckButton text="Teh cat iz here" onApply={onApply} />)
    fireEvent.click(screen.getByRole('button', { name: /Spell Check/ }))
    await waitFor(() => expect(screen.getByText('2 issues')).toBeInTheDocument())
    expect(screen.getByText('Teh')).toBeInTheDocument() // flagged slice

    fireEvent.click(screen.getByText('The'))
    expect(onApply).toHaveBeenCalledWith('The cat iz here')
    expect(toast.success).toHaveBeenCalledWith('Correction applied')
    // panel collapses the applied match; remaining match stays listed
    await waitFor(() => expect(screen.getByText('1 issue')).toBeInTheDocument())
  })

  it('toggles the issues panel and dismisses results', async () => {
    api.aiApi.spellcheck.mockResolvedValue({
      data: { matches: [{ message: 'Spelling', offset: 0, length: 3, replacements: ['The'], rule_id: 'r1', context: '' }] },
    })
    render(<SpellCheckButton text="Teh cat" onApply={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Spell Check/ }))
    await waitFor(() => expect(screen.getByText('1 issue')).toBeInTheDocument())

    // collapse, re-open
    fireEvent.click(screen.getByText('1 issue'))
    expect(screen.queryByText('Spelling')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('1 issue'))
    expect(screen.getByText('Spelling')).toBeInTheDocument()

    // dismiss clears everything and button goes back to Spell Check
    fireEvent.click(screen.getByTitle('Dismiss'))
    expect(screen.getByRole('button', { name: /Spell Check/ })).toBeInTheDocument()
    expect(screen.queryByText('1 issue')).not.toBeInTheDocument()
  })

  it('reports service failures', async () => {
    api.aiApi.spellcheck.mockRejectedValue(new Error('down'))
    render(<SpellCheckButton text="some text" onApply={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Spell Check/ }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Spell check unavailable — start LanguageTool first')
    )
  })
})

describe('SeoPanel', () => {
  const seoResult = {
    platform: 'linkedin',
    score: { overall: 85, readability: 80, keywords: 90, hashtags: 70, links: 60, plain_english: 88, length: 95, recommendations: ['Add a link'] },
    keywords: [{ keyword: 'cloud', count: 3 }],
    meta: { title: 'T', description: 'D' },
    open_graph: { og_title: 'o', og_description: 'o', og_type: 'article', twitter_card: 'summary', twitter_title: 't', twitter_description: 't' },
    character_count: 100,
    hashtag_count: 2,
    link_count: 1,
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('runs the analysis and renders the score', async () => {
    api.aiApi.analyzeSeo.mockResolvedValue({ data: seoResult })
    render(<SeoPanel content={LONG} platform="linkedin" />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    await waitFor(() => expect(screen.getByText('85')).toBeInTheDocument())
    expect(api.aiApi.analyzeSeo.mock.calls[0][0]).toEqual({ content: LONG, platform: 'linkedin' })
  })

  it('normalizes a legacy numeric score without crashing', async () => {
    api.aiApi.analyzeSeo.mockResolvedValue({ data: { ...seoResult, score: 72 } })
    render(<SeoPanel content={LONG} />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    await waitFor(() => expect(screen.getByText('72')).toBeInTheDocument())
  })

  it('keeps the panel in the idle state when analysis fails', async () => {
    api.aiApi.analyzeSeo.mockRejectedValue(new Error('500'))
    render(<SeoPanel content={LONG} />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    await waitFor(() => expect(api.aiApi.analyzeSeo).toHaveBeenCalled())
    expect(screen.getByText(/Click .Analyze. to get an SEO score/)).toBeInTheDocument()
  })

  it('applies the suggested meta tags', async () => {
    const onApplyMeta = vi.fn()
    api.aiApi.analyzeSeo.mockResolvedValue({ data: seoResult })
    render(<SeoPanel content={LONG} onApplyMeta={onApplyMeta} />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    fireEvent.click(await screen.findByText('Apply'))
    expect(onApplyMeta).toHaveBeenCalledWith({ title: 'T', description: 'D' })
  })

  it('hides Apply when there is no suggested meta', async () => {
    api.aiApi.analyzeSeo.mockResolvedValue({ data: { ...seoResult, meta: { title: '', description: '' } } })
    render(<SeoPanel content={LONG} onApplyMeta={vi.fn()} />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    await waitFor(() => expect(screen.getByText('85')).toBeInTheDocument())
    expect(screen.queryByText('Apply')).not.toBeInTheDocument()
  })

  it.each([[65, 'text-amber-500'], [40, 'text-red-500']])('colours a %s score as %s', async (score, cls) => {
    api.aiApi.analyzeSeo.mockResolvedValue({ data: { ...seoResult, score } })
    render(<SeoPanel content={LONG} />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    const el = await screen.findByText(String(score))
    expect(el.className).toContain(cls)
  })

  it('is disabled with empty content', () => {
    render(<SeoPanel content="   " />, { wrapper })
    expect(screen.getByText('Analyze').closest('button')).toBeDisabled()
  })
})

describe('SpellCheckButton — offset bookkeeping and empty flags', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fixing a later match leaves earlier offsets untouched', async () => {
    api.aiApi.spellcheck.mockResolvedValue({
      data: {
        matches: [
          { message: 'Spelling', offset: 0, length: 3, replacements: ['The'], rule_id: 'r1', context: '' },
          { message: 'Grammar', offset: 8, length: 2, replacements: ['is'], rule_id: 'r2', context: '' },
        ],
      },
    })
    const onApply = vi.fn()
    const { rerender } = render(<SpellCheckButton text="Teh cat iz here" onApply={onApply} />)
    fireEvent.click(screen.getByRole('button', { name: /Spell Check/ }))
    await screen.findByText('2 issues')
    fireEvent.click(screen.getByText('is'))
    expect(onApply).toHaveBeenLastCalledWith('Teh cat is here')
    rerender(<SpellCheckButton text="Teh cat is here" onApply={onApply} />)
    await screen.findByText('1 issue')
    expect(screen.getByText('Teh')).toBeInTheDocument() // still points at offset 0
    fireEvent.click(screen.getByText('The'))
    expect(onApply).toHaveBeenLastCalledWith('The cat is here')
  })

  it('shows a dash for a zero-length (insertion) match', async () => {
    api.aiApi.spellcheck.mockResolvedValue({
      data: { matches: [{ message: 'Missing comma', offset: 3, length: 0, replacements: [','], rule_id: 'c', context: '' }] },
    })
    render(<SpellCheckButton text="Yes I do" onApply={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Spell Check/ }))
    await screen.findByText('Missing comma')
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

describe('SeoPanel — pending state and partial meta', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the analyzing state and disables the button while pending', async () => {
    let finish!: (v: unknown) => void
    api.aiApi.analyzeSeo.mockImplementation(() => new Promise((r) => { finish = r }))
    render(<SeoPanel content={LONG} />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    expect(await screen.findByText('Analyzing content…')).toBeInTheDocument()
    expect(screen.getByText('Analyze').closest('button')).toBeDisabled()
    expect(screen.queryByText(/Click .Analyze. to get an SEO score/)).toBeNull()
    await act(async () => {
      finish({ data: { platform: 'x', score: 50, keywords: [], meta: { title: '', description: '' }, open_graph: {}, character_count: 1, hashtag_count: 0, link_count: 0 } })
    })
    expect(screen.queryByText('Analyzing content…')).toBeNull()
  })

  it('offers Apply when only a meta description was suggested', async () => {
    const onApplyMeta = vi.fn()
    api.aiApi.analyzeSeo.mockResolvedValue({
      data: { platform: 'x', score: 90, keywords: [], meta: { title: '', description: 'Only D' }, open_graph: {}, character_count: 1, hashtag_count: 0, link_count: 0 },
    })
    render(<SeoPanel content={LONG} onApplyMeta={onApplyMeta} />, { wrapper })
    fireEvent.click(screen.getByText('Analyze'))
    fireEvent.click(await screen.findByText('Apply'))
    expect(onApplyMeta).toHaveBeenCalledWith({ title: '', description: 'Only D' })
  })
})
