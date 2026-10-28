import { vi, describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const api = vi.hoisted(() => ({
  contentApi: { createPost: vi.fn().mockResolvedValue({ data: { id: 'p1' } }) },
  accountsApi: { list: vi.fn() },
  getAccessToken: vi.fn(() => 'tok'),
}))
vi.mock('@/services/api', () => api)

import { StepFirstPost } from './StepFirstPost'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const acct = (id: string, platform: string, status = 'active') => ({ id, platform, status })

async function renderWith(accounts: ReturnType<typeof acct>[]) {
  api.accountsApi.list.mockResolvedValue({ data: accounts })
  render(<StepFirstPost onContinue={vi.fn()} />, { wrapper })
  await waitFor(() => expect(api.accountsApi.list).toHaveBeenCalled())
}

function write(text = 'Hello world') {
  fireEvent.change(screen.getByPlaceholderText('What would you like to share?'), {
    target: { value: text },
  })
}

describe('StepFirstPost platform targeting', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends every active account on the picked platforms as targets', async () => {
    await renderWith([
      acct('li-person', 'linkedin'),
      acct('li-org', 'linkedin'),
      acct('fb-page', 'facebook'),
      acct('ig-1', 'instagram'),
    ])
    const li = screen.getByText('LinkedIn')
    await waitFor(() => expect(li).toBeEnabled())
    fireEvent.click(li)
    fireEvent.click(screen.getByText('Facebook'))
    write()
    fireEvent.click(screen.getByText('Create Post'))

    await waitFor(() => expect(api.contentApi.createPost).toHaveBeenCalledTimes(1))
    expect(api.contentApi.createPost).toHaveBeenCalledWith({
      content_text: 'Hello world',
      metadata: { source: 'onboarding' },
      target_account_ids: ['li-person', 'li-org', 'fb-page'],
    })
  })

  it('never targets an expired or disconnected account', async () => {
    await renderWith([
      acct('li-ok', 'linkedin'),
      acct('li-expired', 'linkedin', 'expired'),
      acct('fb-dead', 'facebook', 'disconnected'),
    ])
    const li = screen.getByText('LinkedIn')
    await waitFor(() => expect(li).toBeEnabled())
    // A platform whose only account is disconnected can't be picked.
    expect(screen.getByText('Facebook').closest('button')).toBeDisabled()
    fireEvent.click(li)
    write()
    fireEvent.click(screen.getByText('Create Post'))

    await waitFor(() => expect(api.contentApi.createPost).toHaveBeenCalled())
    expect(api.contentApi.createPost.mock.calls[0][0].target_account_ids).toEqual(['li-ok'])
  })

  it('disables platforms with no connected account and says why', async () => {
    await renderWith([acct('tw-1', 'twitter')])
    const tw = screen.getByText('Twitter / X')
    await waitFor(() => expect(tw).toBeEnabled())

    const ig = screen.getByText('Instagram').closest('button')!
    expect(ig).toBeDisabled()
    expect(ig).toHaveAttribute('title', 'Connect your Instagram account to target it')
    expect(ig).toHaveTextContent('(not connected)')
    fireEvent.click(ig)
    expect(ig).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText(/Greyed-out platforms aren’t connected yet/)).toBeInTheDocument()

    fireEvent.click(tw)
    expect(tw).toHaveAttribute('aria-pressed', 'true')
  })

  it('omits targets when nothing is picked, keeping a plain draft', async () => {
    await renderWith([acct('li-1', 'linkedin')])
    write()
    fireEvent.click(screen.getByText('Create Post'))

    await waitFor(() => expect(api.contentApi.createPost).toHaveBeenCalledWith({
      content_text: 'Hello world',
      metadata: { source: 'onboarding' },
    }))
  })

  it('hides the not-connected hint once every platform is connected', async () => {
    await renderWith([
      acct('a', 'linkedin'), acct('b', 'facebook'), acct('c', 'twitter'), acct('d', 'instagram'),
    ])
    await waitFor(() => expect(screen.getByText('Instagram')).toBeEnabled())
    expect(screen.queryByText(/Greyed-out platforms/)).toBeNull()
  })

  it('treats a failed accounts load as nothing connected', async () => {
    api.accountsApi.list.mockRejectedValue(new Error('down'))
    render(<StepFirstPost onContinue={vi.fn()} />, { wrapper })
    await waitFor(() => expect(api.accountsApi.list).toHaveBeenCalled())
    for (const label of ['LinkedIn', 'Facebook', 'Twitter / X', 'Instagram']) {
      expect(screen.getByText(label).closest('button')).toBeDisabled()
    }
  })
})
