import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { authApi, setTokens, clearTokens, getAccessToken } from '@/services/api'
import type { User, AuthState, LoginCredentials, RegisterData, TokenResponse } from '@/types'
import toast from 'react-hot-toast'

interface AuthContextType extends AuthState {
  login: (credentials: LoginCredentials) => Promise<boolean>
  register: (data: RegisterData) => Promise<boolean>
  logout: () => void
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    access_token: getAccessToken(),
    refresh_token: localStorage.getItem('refresh_token'),
    isAuthenticated: !!getAccessToken(),
    isLoading: true,
  })

  const queryClient = useQueryClient()

  const fetchUser = useCallback(async () => {
    if (!getAccessToken()) {
      setState(prev => ({ ...prev, isLoading: false, isAuthenticated: false }))
      return
    }

    try {
      const response = await authApi.me()
      setState(prev => ({
        ...prev,
        user: response.data,
        isAuthenticated: true,
        isLoading: false,
      }))
    } catch (error) {
      clearTokens()
      setState(prev => ({
        ...prev,
        user: null,
        isAuthenticated: false,
        isLoading: false,
      }))
    }
  }, [])

  useEffect(() => {
    fetchUser()
  }, [fetchUser])

  const login = async (credentials: LoginCredentials): Promise<boolean> => {
    try {
      const response = await authApi.login(credentials)
      const { access_token, refresh_token } = response.data
      setTokens(access_token, refresh_token)
      await fetchUser()
      toast.success('Welcome back!')
      return true
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Invalid credentials')
      return false
    }
  }

  const register = async (data: RegisterData): Promise<boolean> => {
    try {
      await authApi.register(data)
      // Auto-login after registration
      return await login({ email: data.email, password: data.password })
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Registration failed')
      return false
    }
  }

  const logout = () => {
    clearTokens()
    queryClient.clear()
    setState({
      user: null,
      access_token: null,
      refresh_token: null,
      isAuthenticated: false,
      isLoading: false,
    })
    toast.success('Logged out successfully')
  }

  const refreshUser = fetchUser

  return (
    <AuthContext.Provider value={{ ...state, login, register, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

export function useRequireAuth() {
  const auth = useAuth()
  if (!auth.isLoading && !auth.isAuthenticated) {
    window.location.href = '/login'
  }
  return auth
}