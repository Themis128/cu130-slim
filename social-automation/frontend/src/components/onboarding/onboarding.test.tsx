import { vi, describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), custom: vi.fn(), dismiss: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const api = vi.hoisted(() => ({
  contentApi: { createPost: vi.fn().mockResolvedValue({ data: { id: 'p1' } }) },
  accountsApi: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    connect: vi.fn().mockResolvedValue({ data: { authorization_url: 'https://oauth.example/go' } }),
  },
  getAccessToken: vi.fn(() => 'tok'),
}))
vi.mock('@/services/api', () => api)

import { Wizard } from './Wizard'
import { StepBrandBasics } from './StepBrandBasics'
import { StepConnectAccount } from './StepConnectAccount'
import { StepFirstPost } from './StepFirstPost'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('Wizard', () => {
  const steps = [
    { title: 'One', render: () => <div>step-1-body</div> },
    { title: 'Two', render: () => <div>step-2-body</div> },
    { title: 'Done', render: ({ next }: { next: () => void }) => <button onClick={next}>finish</button> },
  ]

  it('advances through steps, shows progress, and completes on the last step', () => {
    const onComplete = vi.fn()
    render(<Wizard steps={steps} onComplete={onComplete} />)

    expect(screen.getByText('Step 1 of 3')).toBeInTheDocument()
    expect(screen.getByText('step-1-body')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Continue'))
    expect(screen.getByText('Step 2 of 3')).toBeInTheDocument()
    expect(screen.getByText('step-2-body')).toBeInTheDocument()

    // Back returns to step 1
    fireEvent.click(screen.getByText('Back'))
    expect(screen.getByText('step-1-body')).toBeInTheDocument()
    // Back is disabled on the first step
    expect(screen.getByText('Back').closest('button')).toBeDisabled()

    // Walk to the last step and complete
    fireEvent.click(screen.getByText('Continue'))
    fireEvent.click(screen.getByText('Continue'))
    expect(screen.getByText('Step 3 of 3')).toBeInTheDocument()
    fireEvent.click(screen.getByText('finish'))
    expect(onComplete).toHaveBeenCalled()
  })
})

describe('StepBrandBasics', () => {
  it('submits brand data and supports skipping', () => {
    const onSubmit = vi.fn()
    const onSkip = vi.fn()
    render(<StepBrandBasics onSubmit={onSubmit} onSkip={onSkip} />)

    // Continue is disabled without a brand name
    expect(screen.getByText('Continue').closest('button')).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText('e.g. Acme Inc.'), { target: { value: 'Cloudless' } })
    fireEvent.change(screen.getByLabelText('Industry'), { target: { value: 'Technology' } })
    fireEvent.click(screen.getByText('Casual'))

    fireEvent.click(screen.getByText('Continue'))
    expect(onSubmit).toHaveBeenCalledWith({ brandName: 'Cloudless', industry: 'Technology', tone: 'Casual' })

    fireEvent.click(screen.getByText('Skip for now'))
    expect(onSkip).toHaveBeenCalled()
  })
})

describe('StepConnectAccount', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.accountsApi.list.mockResolvedValue({ data: [] })
    delete window.location
    window.location = { href: '' } as Location
  })

  it('lists platform buttons and warns when nothing is connected', async () => {
    render(<StepConnectAccount onContinue={vi.fn()} />, { wrapper })
    await screen.findByText('LinkedIn')
    expect(screen.getByText(/won’t be able to publish/)).toBeInTheDocument()
    expect(screen.getByText('TikTok')).toBeInTheDocument()
  })

  it('redirects to the authorization URL on connect', async () => {
    render(<StepConnectAccount onContinue={vi.fn()} />, { wrapper })
    fireEvent.click(await screen.findByText('LinkedIn'))
    await waitFor(() => expect(window.location.href).toBe('https://oauth.example/go'))
  })

  it('toasts when the connect call fails', async () => {
    api.accountsApi.connect.mockRejectedValueOnce({ response: { data: { detail: 'nope' } } })
    render(<StepConnectAccount onContinue={vi.fn()} />, { wrapper })
    fireEvent.click(await screen.findByText('Twitter / X'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
  })

  it('summarizes already-connected accounts', async () => {
    api.accountsApi.list.mockResolvedValue({
      data: [{ id: 'a1', platform: 'linkedin', display_name: 'My Page', status: 'active' }],
    })
    render(<StepConnectAccount onContinue={vi.fn()} />, { wrapper })
    await screen.findByText('1 account connected')
    expect(screen.getByText('My Page')).toBeInTheDocument()
    // the linkedin tile shows Connected state
    expect(screen.getByText('Connected')).toBeInTheDocument()
  })
})

describe('StepFirstPost', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('toggles platforms and saves the draft', async () => {
    api.accountsApi.list.mockResolvedValueOnce({ data: [{ id: 'a-li', platform: 'linkedin', status: 'active' }] })
    const onContinue = vi.fn()
    render(<StepFirstPost onContinue={onContinue} />, { wrapper })

    const li = screen.getByText('LinkedIn')
    await waitFor(() => expect(li).toBeEnabled())
    fireEvent.click(li)
    expect(li).toHaveClass('border-primary')
    fireEvent.click(li)
    expect(li).not.toHaveClass('border-primary')

    fireEvent.change(screen.getByPlaceholderText('What would you like to share?'), {
      target: { value: 'My first post' },
    })
    fireEvent.click(screen.getByText('Create Post'))

    await waitFor(() => expect(api.contentApi.createPost).toHaveBeenCalledWith({
      content_text: 'My first post',
      metadata: { source: 'onboarding' },
    }))
    await screen.findByText('Draft post created!')
    fireEvent.click(screen.getByText('Continue'))
    expect(onContinue).toHaveBeenCalled()
  })

  it('skips without creating', () => {
    const onContinue = vi.fn()
    render(<StepFirstPost onContinue={onContinue} />, { wrapper })
    fireEvent.click(screen.getByText('Skip for now'))
    expect(onContinue).toHaveBeenCalled()
    expect(api.contentApi.createPost).not.toHaveBeenCalled()
  })

  it('includes the server detail when the save is rejected with one', async () => {
    api.contentApi.createPost.mockRejectedValueOnce({ response: { data: { detail: 'Content blocked by policy' } } })
    render(<StepFirstPost onContinue={vi.fn()} />, { wrapper })
    fireEvent.change(screen.getByPlaceholderText('What would you like to share?'), { target: { value: 'x' } })
    fireEvent.click(screen.getByText('Create Post'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(toast.error.mock.calls[0][0]).toBe('Couldn’t save your draft.\nDetails: Content blocked by policy')
    expect(screen.queryByText('Draft post created!')).toBeNull()
  })

  it('toasts when the save fails', async () => {
    api.contentApi.createPost.mockRejectedValueOnce(new Error('500'))
    render(<StepFirstPost onContinue={vi.fn()} />, { wrapper })
    fireEvent.change(screen.getByPlaceholderText('What would you like to share?'), { target: { value: 'x' } })
    fireEvent.click(screen.getByText('Create Post'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
  })
})
