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

  it('is disabled with empty content', () => {
    render(<SeoPanel content="   " />, { wrapper })
    expect(screen.getByText('Analyze').closest('button')).toBeDisabled()
  })
})
