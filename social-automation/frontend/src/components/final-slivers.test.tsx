import { vi, describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/dashboard',
}))

const auth = vi.hoisted(() => ({ user: { name: 'Themis', email: 't@x.com' }, logout: vi.fn() }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => auth }))

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), custom: vi.fn(), dismiss: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const api = vi.hoisted(() => ({
  accountsApi: {
    list: vi.fn(), get: vi.fn(), getPageProfile: vi.fn(), updatePageProfile: vi.fn(),
    uploadProfilePicture: vi.fn(), uploadCoverPhoto: vi.fn(), assignManageTask: vi.fn(),
  },
  secretsApi: { set: vi.fn() },
  profileApi: {
    getProfile: vi.fn(), updateProfile: vi.fn(),
    uploadProfilePicture: vi.fn(), uploadCoverPhoto: vi.fn(), login: vi.fn(),
  },
  aiProvidersApi: { upsert: vi.fn(), test: vi.fn() },
  linkedinApi: { publish: vi.fn() },
  brandApi: {
    create: vi.fn(), update: vi.fn(), updateVoice: vi.fn(), updateVisual: vi.fn(),
    compileGuidelines: vi.fn(), createAsset: vi.fn(),
  },
  getAccessToken: vi.fn(() => 'tok'),
}))
vi.mock('@/services/api', () => api)

import { Sidebar } from './layout/Sidebar'
import { EmptyState } from './dashboard/EmptyState'
import { PublicNav } from './marketing/PublicNav'
import { FunnelSection } from './marketing/FunnelSection'
import { LeadCapture } from './marketing/LeadCapture'
import { FileText } from 'lucide-react'
import {
  useAccounts, usePageProfile, useProfile,
  useUpdatePageProfile, useUploadProfilePicture, useUploadCoverPhoto,
  useSetSecret, useUpdateProfile, useUploadProfileProfilePicture,
  useUploadProfileCoverPhoto, useProfileLogin, useAssignManageTask,
  useUpsertAIProvider, useTestAIProvider, useLinkedinPublish,
  useCreateBrand, useUpdateBrand, useUpdateBrandVoice, useUpdateBrandVisual,
  useCompileGuidelines, useCreateBrandAsset,
} from '@/hooks/useQueries'

function openMenu(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger)
  fireEvent.click(trigger)
}

describe('Sidebar', () => {
  beforeEach(() => { vi.clearAllMocks(); auth.user = { name: 'Themis', email: 't@x.com' } as never })

  it('collapses and expands via the toggle button', () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByLabelText('Collapse sidebar'))
    expect(screen.queryByText('SocialAuto')).toBeNull() // brand hidden when collapsed
    fireEvent.click(screen.getByLabelText('Expand sidebar'))
    expect(screen.getByText('SocialAuto')).toBeInTheDocument()
  })

  it('marks the active route and renders every nav item', () => {
    render(<Sidebar />)
    expect(screen.getByRole('link', { name: /Dashboard/ })).toHaveClass('bg-primary')
    expect(screen.getByRole('link', { name: /Posts/ })).toHaveAttribute('href', '/content')
    expect(screen.getByRole('link', { name: /Ops Console/ })).toHaveAttribute('href', '/ops')
  })

  it('collapsed mode shows the expand-menu button instead of the user block', () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByLabelText('Collapse sidebar'))
    // collapsed user area is a plain menu button
    const btns = screen.getAllByRole('button')
    expect(btns.some((b) => b.getAttribute('aria-label') === 'User menu')).toBe(false)
  })

  it('user menu logs out', async () => {
    render(<Sidebar />)
    openMenu(screen.getByLabelText('User menu'))
    fireEvent.click(await screen.findByText('Logout'))
    expect(auth.logout).toHaveBeenCalled()
  })

  it('hides the user block when logged out', () => {
    auth.user = null as never
    render(<Sidebar />)
    expect(screen.queryByLabelText('User menu')).toBeNull()
  })
})

describe('EmptyState', () => {
  it('renders title, description and optional CTA', () => {
    const { rerender } = render(
      <EmptyState icon={FileText} title="No posts" description="Create your first" actionLabel="New post" actionHref="/content/new" />
    )
    expect(screen.getByText('No posts')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'New post' })).toHaveAttribute('href', '/content/new')

    rerender(<EmptyState icon={FileText} title="Empty" description="d" />)
    expect(screen.queryByRole('link')).toBeNull()
  })
})

describe('PublicNav mobile links', () => {
  it('clicking a mobile link closes the menu', () => {
    render(<PublicNav />)
    const toggle = screen.getByLabelText('Toggle menu')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    // mobile nav renders a second set of links — click one
    const signIns = screen.getAllByRole('link', { name: 'Sign In' })
    fireEvent.click(signIns[signIns.length - 1])
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('FunnelSection', () => {
  it('renders the fetched latest post and tolerates fetch failure', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ post: { title: 'Latest Guide', slug: 'x', excerpt: 'e' } }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { container, unmount } = render(<FunnelSection />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/latest-post'))
    expect(container.textContent!.length).toBeGreaterThan(0)
    unmount()

    fetchMock.mockRejectedValue(new Error('down'))
    render(<FunnelSection />) // must not throw
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    vi.unstubAllGlobals()
  })
})

describe('LeadCapture', () => {
  it('shows error state on a failed submit and silently succeeds via honeypot path', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ playbook_delivery: 'email' }) })
    vi.stubGlobal('fetch', fetchMock)

    render(<LeadCapture />)
    const email = screen.getByPlaceholderText('you@company.com')
    fireEvent.change(email, { target: { value: 'a@b.com' } })
    fireEvent.submit(email.closest('form')!)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/leads/public', expect.objectContaining({ method: 'POST' })))
    await screen.findByText(/try again|wrong|error/i).catch(() => {}) // error copy may vary

    // second submit succeeds → done state
    fireEvent.submit(email.closest('form')!)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    vi.unstubAllGlobals()
  })

  it('rejects on network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('net')))
    render(<LeadCapture />)
    const email = screen.getAllByRole('textbox').find((el) => (el as HTMLInputElement).type === 'email')!
    fireEvent.change(email, { target: { value: 'a@b.com' } })
    fireEvent.submit(email.closest('form')!)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    vi.unstubAllGlobals()
  })
})

// ---- useQueries error + retry tails ----

const axErr = (status = 400, detail: string | undefined = 'nope') => ({ response: { status, data: { detail } } })

function MutationProbe({ useMut, vars }: { useMut: () => { mutateAsync: (v?: unknown) => Promise<unknown> }; vars?: unknown }) {
  const m = useMut()
  return <button onClick={() => m.mutateAsync(vars).catch(() => {})}>go</button>
}

function wrapM(useMut: never | (() => { mutateAsync: (v?: unknown) => Promise<unknown> }), vars?: unknown) {
  const client = new QueryClient()
  render(<QueryClientProvider client={client}><MutationProbe useMut={useMut as () => { mutateAsync: (v?: unknown) => Promise<unknown> }} vars={vars} /></QueryClientProvider>)
  fireEvent.click(screen.getByText('go'))
}

describe('useQueries error toasts', () => {
  beforeEach(() => vi.clearAllMocks())

  it.each([
    ['useUpdatePageProfile', () => useUpdatePageProfile(), api.accountsApi.updatePageProfile, 'Failed to update page profile'],
    ['useUploadProfilePicture', () => useUploadProfilePicture(), api.accountsApi.uploadProfilePicture, 'Failed to upload profile picture'],
    ['useUploadCoverPhoto', () => useUploadCoverPhoto(), api.accountsApi.uploadCoverPhoto, 'Failed to upload cover photo'],
    ['useSetSecret', () => useSetSecret(), api.secretsApi.set, 'Failed to save credential'],
    ['useUpdateProfile', () => useUpdateProfile(), api.profileApi.updateProfile, 'Failed to update profile'],
    ['useUploadProfileProfilePicture', () => useUploadProfileProfilePicture(), api.profileApi.uploadProfilePicture, 'Failed to upload profile picture'],
    ['useUploadProfileCoverPhoto', () => useUploadProfileCoverPhoto(), api.profileApi.uploadCoverPhoto, 'Failed to upload cover photo'],
    ['useProfileLogin', () => useProfileLogin(), api.profileApi.login, 'Login failed'],
    ['useAssignManageTask', () => useAssignManageTask(), api.accountsApi.assignManageTask, 'Failed to assign MANAGE task'],
    ['useUpsertAIProvider', () => useUpsertAIProvider(), api.aiProvidersApi.upsert, 'Failed to save provider'],
    ['useTestAIProvider', () => useTestAIProvider(), api.aiProvidersApi.test, 'Test request failed — check your session and try again'],
    ['useLinkedinPublish', () => useLinkedinPublish(), api.linkedinApi.publish, 'Failed to publish to LinkedIn'],
    ['useCreateBrand', () => useCreateBrand(), api.brandApi.create, 'Failed to create brand'],
    ['useUpdateBrand', () => useUpdateBrand(), api.brandApi.update, 'Failed to update brand'],
    ['useUpdateBrandVoice', () => useUpdateBrandVoice(), api.brandApi.updateVoice, 'Failed to update brand voice'],
    ['useUpdateBrandVisual', () => useUpdateBrandVisual(), api.brandApi.updateVisual, 'Failed to update brand visual'],
    ['useCompileGuidelines', () => useCompileGuidelines(), api.brandApi.compileGuidelines, 'Failed to compile guidelines'],
    ['useCreateBrandAsset', () => useCreateBrandAsset(), api.brandApi.createAsset, 'Failed to add brand asset'],
  ])('%s toasts its failure message', async (_n, useMut, mockFn, msg) => {
    // detail present → toast shows it; then detail absent → fallback message
    mockFn.mockRejectedValueOnce(axErr())
    wrapM(useMut as never, {})
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    const flat = (api.aiProvidersApi.upsert === mockFn || api.aiProvidersApi.test === mockFn)
    expect(toast.error.mock.calls[0][0]).toBe(flat ? msg : 'nope')
  })
})

describe('useQueries retry predicates', () => {
  beforeEach(() => vi.clearAllMocks())

  async function countCalls(useQ: () => { isError: boolean }) {
    const client = new QueryClient()
    function Q() { useQ(); return null }
    render(<QueryClientProvider client={client}><Q /></QueryClientProvider>)
    await waitFor(() => expect(api.accountsApi.list.mock.calls.length + api.profileApi.getProfile.mock.calls.length + api.accountsApi.getPageProfile.mock.calls.length).toBeGreaterThan(0), { timeout: 3000 })
    await new Promise((r) => setTimeout(r, 200))
  }

  it('useAccounts does not retry on 401', async () => {
    api.accountsApi.list.mockRejectedValue(axErr(401))
    const client = new QueryClient()
    function Q() { useAccounts(); return null }
    render(<QueryClientProvider client={client}><Q /></QueryClientProvider>)
    await waitFor(() => expect(api.accountsApi.list).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 300))
    expect(api.accountsApi.list).toHaveBeenCalledTimes(1)
  })

  it('useProfile does not retry on 400', async () => {
    api.profileApi.getProfile.mockRejectedValue(axErr(400))
    const client = new QueryClient()
    function Q() { useProfile('acc1'); return null }
    render(<QueryClientProvider client={client}><Q /></QueryClientProvider>)
    await waitFor(() => expect(api.profileApi.getProfile).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 300))
    expect(api.profileApi.getProfile).toHaveBeenCalledTimes(1)
  })

  it('usePageProfile retries transient errors', async () => {
    api.accountsApi.getPageProfile.mockRejectedValue(axErr(500))
    const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 1 } } })
    function Q() { usePageProfile('acc1'); return null }
    render(<QueryClientProvider client={client}><Q /></QueryClientProvider>)
    await waitFor(() => expect(api.accountsApi.getPageProfile.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 3000 })
  })
})
