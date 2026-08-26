import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'
import { AuthProvider, useAuth } from '@/hooks/useAuth'
import { authApi } from '@/services/api'
import { setTokens, clearTokens, getAccessToken, getRefreshToken } from '@/services/api'

vi.mock('@/services/api', () => ({
  authApi: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    refresh: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
  },
  setTokens: vi.fn(),
  clearTokens: vi.fn(),
  getAccessToken: vi.fn(),
  getRefreshToken: vi.fn(),
}))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  )
}

describe('useAuth', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getAccessToken).mockReturnValue(null)
    vi.mocked(getRefreshToken).mockReturnValue(null)
  })

  it('initializes with unauthenticated state when no token', () => {
    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })
    expect(result.current.isAuthenticated).toBe(false)
    expect(result.current.user).toBeNull()
    expect(result.current.isLoading).toBe(false)
  })

  it('initializes with authenticated state when token exists', async () => {
    vi.mocked(getAccessToken).mockReturnValue('test-token')
    vi.mocked(getRefreshToken).mockReturnValue('refresh-token')
    vi.mocked(authApi.me).mockResolvedValue({ data: { id: '1', email: 'test@test.com', name: 'Test User' } })

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    expect(result.current.isLoading).toBe(true)

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
      expect(result.current.isAuthenticated).toBe(true)
      expect(result.current.user).toEqual({ id: '1', email: 'test@test.com', name: 'Test User' })
    })
  })

  it('logs in successfully and updates state', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      data: { access_token: 'new-access', refresh_token: 'new-refresh', user: { id: '1', email: 'test@test.com', name: 'Test User' } },
    })
    vi.mocked(authApi.me).mockResolvedValue({ data: { id: '1', email: 'test@test.com', name: 'Test User' } })
    // After login, getAccessToken should return the new token
    vi.mocked(getAccessToken).mockReturnValue('new-access')

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    let success = false
    await act(async () => {
      success = await result.current.login({ email: 'test@test.com', password: 'password123' })
    })

    expect(success).toBe(true)
    expect(setTokens).toHaveBeenCalledWith('new-access', 'new-refresh')
    expect(authApi.me).toHaveBeenCalled()
    expect(result.current.isAuthenticated).toBe(true)
  })

  it('handles login failure', async () => {
    vi.mocked(authApi.login).mockRejectedValue({
      response: { data: { detail: 'Invalid credentials' } },
    })

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    let success = true
    await act(async () => {
      success = await result.current.login({ email: 'test@test.com', password: 'wrong' })
    })

    expect(success).toBe(false)
    expect(result.current.isAuthenticated).toBe(false)
  })

  it('registers successfully', async () => {
    vi.mocked(authApi.register).mockResolvedValue({
      data: { access_token: 'new-access', refresh_token: 'new-refresh', user: { id: '1', email: 'new@test.com', name: 'New User' } },
    })
    vi.mocked(authApi.login).mockResolvedValue({
      data: { access_token: 'new-access', refresh_token: 'new-refresh', user: { id: '1', email: 'new@test.com', name: 'New User' } },
    })
    vi.mocked(authApi.me).mockResolvedValue({ data: { id: '1', email: 'new@test.com', name: 'New User' } })
    vi.mocked(getAccessToken).mockReturnValue('new-access')

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    let success = false
    await act(async () => {
      success = await result.current.register({ email: 'new@test.com', password: 'password123', full_name: 'New User' })
    })

    expect(success).toBe(true)
    expect(setTokens).toHaveBeenCalled()
  })

  it('logs out and clears state', async () => {
    vi.mocked(getAccessToken).mockReturnValue('test-token')
    vi.mocked(authApi.me).mockResolvedValue({ data: { id: '1', email: 'test@test.com', name: 'Test User' } })

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => {
      result.current.logout()
    })

    expect(clearTokens).toHaveBeenCalled()
    expect(result.current.isAuthenticated).toBe(false)
    expect(result.current.user).toBeNull()
  })

  it('refreshes user data', async () => {
    vi.mocked(getAccessToken).mockReturnValue('test-token')
    vi.mocked(authApi.me)
      .mockResolvedValueOnce({ data: { id: '1', email: 'test@test.com', name: 'Test User' } })
      .mockResolvedValueOnce({ data: { id: '1', email: 'updated@test.com', name: 'Updated User' } })

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.user?.email).toBe('test@test.com'))

    await act(async () => {
      await result.current.refreshUser()
    })

    expect(result.current.user?.email).toBe('updated@test.com')
  })

  it('updates profile', async () => {
    vi.mocked(getAccessToken).mockReturnValue('test-token')
    vi.mocked(authApi.me).mockResolvedValue({ data: { id: '1', email: 'test@test.com', name: 'Test User' } })
    vi.mocked(authApi.updateProfile).mockResolvedValue({ data: { id: '1', email: 'test@test.com', name: 'Updated Name' } })

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    let success = false
    await act(async () => {
      success = await result.current.updateProfile({ full_name: 'Updated Name' })
    })

    expect(success).toBe(true)
    expect(authApi.updateProfile).toHaveBeenCalledWith({ full_name: 'Updated Name' })
  })

  it('changes password', async () => {
    vi.mocked(getAccessToken).mockReturnValue('test-token')
    vi.mocked(authApi.me).mockResolvedValue({ data: { id: '1', email: 'test@test.com', name: 'Test User' } })
    vi.mocked(authApi.changePassword).mockResolvedValue({ data: { success: true } })

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    let success = false
    await act(async () => {
      success = await result.current.changePassword('oldpass', 'newpass123')
    })

    expect(success).toBe(true)
    expect(authApi.changePassword).toHaveBeenCalledWith({ current_password: 'oldpass', new_password: 'newpass123' })
  })
})