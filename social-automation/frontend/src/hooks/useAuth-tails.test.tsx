import { vi, describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), custom: vi.fn(), dismiss: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const api = vi.hoisted(() => ({
  authApi: {
    me: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
  },
  setTokens: vi.fn(),
  clearTokens: vi.fn(),
  getAccessToken: vi.fn(() => 'tok'),
  getRefreshToken: vi.fn(() => 'rtok'),
}))
vi.mock('@/services/api', () => api)

import { AuthProvider, useAuth, useRequireAuth } from './useAuth'

function Probe() {
  const auth = useAuth()
  return (
    <div>
      <span data-testid="auth">{String(auth.isAuthenticated)}</span>
      <span data-testid="loading">{String(auth.isLoading)}</span>
      <span data-testid="user">{auth.user?.email ?? 'none'}</span>
      <button onClick={() => auth.login({ email: 'e@x.com', password: 'p' })}>login</button>
      <button onClick={() => auth.register({ email: 'e@x.com', password: 'p', name: 'N' })}>reg</button>
      <button onClick={() => auth.logout()}>logout</button>
      <button onClick={() => auth.updateProfile({ full_name: 'X' })}>upd</button>
      <button onClick={() => auth.changePassword('o', 'n')}>pwd</button>
      <button onClick={() => auth.refreshUser()}>refresh</button>
    </div>
  )
}

function ReqProbe() {
  const auth = useRequireAuth()
  return <span data-testid="req">{String(auth.isAuthenticated)}</span>
}

function wrap(children: ReactNode) {
  const client = new QueryClient()
  return render(<QueryClientProvider client={client}><AuthProvider>{children}</AuthProvider></QueryClientProvider>)
}

describe('useAuth tails', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getAccessToken.mockReturnValue('tok')
    api.authApi.me.mockResolvedValue({ data: { id: '1', email: 'u@x.com' } })
    delete window.location
    window.location = { href: '' } as Location
  })

  it('fetchUser clears tokens on 401', async () => {
    api.authApi.me.mockRejectedValue({ response: { status: 401 } })
    wrap(<Probe />)
    await waitFor(() => expect(api.authApi.me).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByTestId('auth').textContent).toBe('false'))
    expect(api.clearTokens).toHaveBeenCalled()
  })

  it('fetchUser keeps tokens on non-auth errors', async () => {
    api.authApi.me.mockRejectedValue({ response: { status: 500 } })
    wrap(<Probe />)
    await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
    expect(screen.getByTestId('auth').textContent).toBe('true') // token still present
    expect(api.clearTokens).not.toHaveBeenCalled()
  })

  it('skips fetchUser when there is no token', async () => {
    api.getAccessToken.mockReturnValue(null)
    wrap(<Probe />)
    await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
    expect(api.authApi.me).not.toHaveBeenCalled()
  })

  it('login returns 2fa_required on that detail', async () => {
    api.authApi.login.mockRejectedValue({ response: { data: { detail: 'two_factor_required' } } })
    api.authApi.me.mockResolvedValue({ data: {} })
    let result!: string
    function RProbe() {
      const auth = useAuth()
      return <button onClick={async () => { result = await auth.login({ email: 'e', password: 'p' }) }}>go</button>
    }
    wrap(<RProbe />)
    await waitFor(() => expect(api.authApi.me).toHaveBeenCalled())
    await act(async () => { fireEvent.click(screen.getByText('go')) })
    expect(result).toBe('2fa_required')
  })

  it('login returns error and toasts on bad credentials', async () => {
    api.authApi.login.mockRejectedValue({ response: { data: { detail: 'Invalid credentials' } } })
    let result!: string
    function RProbe() {
      const auth = useAuth()
      return <button onClick={async () => { result = await auth.login({ email: 'e', password: 'p' }) }}>go</button>
    }
    wrap(<RProbe />)
    await waitFor(() => expect(api.authApi.me).toHaveBeenCalled())
    await act(async () => { fireEvent.click(screen.getByText('go')) })
    expect(result).toBe('error')
    expect(toast.error).toHaveBeenCalledWith('Invalid credentials')
  })

  it('register failure toasts and returns false', async () => {
    api.authApi.register.mockRejectedValue({ response: { data: { detail: 'email taken' } } })
    let result!: boolean
    function RProbe() {
      const auth = useAuth()
      return <button onClick={async () => { result = await auth.register({ email: 'e', password: 'p', name: 'N' }) }}>go</button>
    }
    wrap(<RProbe />)
    await waitFor(() => expect(api.authApi.me).toHaveBeenCalled())
    await act(async () => { fireEvent.click(screen.getByText('go')) })
    expect(result).toBe(false)
    expect(toast.error).toHaveBeenCalledWith('email taken')
  })

  it('updateProfile / changePassword failures toast and return false', async () => {
    api.authApi.updateProfile.mockRejectedValue({ response: { data: { detail: 'bad' } } })
    api.authApi.changePassword.mockRejectedValue({ response: { data: {} } })
    let r1!: boolean, r2!: boolean
    function RProbe() {
      const auth = useAuth()
      return (
        <>
          <button onClick={async () => { r1 = await auth.updateProfile({}) }}>upd</button>
          <button onClick={async () => { r2 = await auth.changePassword('a', 'b') }}>pwd</button>
        </>
      )
    }
    wrap(<RProbe />)
    await waitFor(() => expect(api.authApi.me).toHaveBeenCalled())
    await act(async () => { fireEvent.click(screen.getByText('upd')) })
    await act(async () => { fireEvent.click(screen.getByText('pwd')) })
    expect(r1).toBe(false)
    expect(r2).toBe(false)
    expect(toast.error).toHaveBeenCalledWith('bad')
    expect(toast.error).toHaveBeenCalledWith('Failed to change password')
  })

  it('useAuth throws outside the provider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow('useAuth must be used within an AuthProvider')
    spy.mockRestore()
  })

  it('useRequireAuth redirects to /login when unauthenticated', async () => {
    api.getAccessToken.mockReturnValue(null)
    wrap(<ReqProbe />)
    await waitFor(() => expect(window.location.href).toBe('/login'))
  })
})
