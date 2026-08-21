import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { Toaster } from 'react-hot-toast'
import { AuthProvider } from '@/hooks/useAuth'
import { ThemeProvider } from '@/hooks/useTheme'

// Create mock values using vi.hoisted to avoid hoisting issues
const { mockAuthValue, mockThemeValue, mockRouter, mockSearchParams, mockPathname } = vi.hoisted(() => {
  const mockAuthValue = {
    user: { id: '1', name: 'Test User', email: 'test@example.com', avatar_url: null },
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
    updateProfile: vi.fn().mockResolvedValue(true),
    changePassword: vi.fn().mockResolvedValue(true),
    isAuthenticated: true,
    isLoading: false,
  }

  const mockThemeValue = {
    theme: 'light',
    resolvedTheme: 'light',
    setTheme: vi.fn(),
    toggleTheme: vi.fn(),
    mounted: true,
  }

  const mockRouter = {
    push: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
  }

  const mockSearchParams = {
    get: vi.fn(() => null),
  }

  const mockPathname = '/dashboard'

  return { mockAuthValue, mockThemeValue, mockRouter, mockSearchParams, mockPathname }
})

// Create contexts
const AuthContext = { Provider: ({ value, children }: { value: typeof mockAuthValue; children: ReactNode }) => <>{children}</> }
const ThemeContext = { Provider: ({ value, children }: { value: typeof mockThemeValue; children: ReactNode }) => <>{children}</> }

// Mock hooks
vi.mock('@/hooks/useAuth', () => ({
  useAuth: vi.fn(() => mockAuthValue),
  AuthProvider: vi.fn(({ children }: { children: ReactNode }) => <AuthContext.Provider value={mockAuthValue}>{children}</AuthContext.Provider>),
}))

vi.mock('@/hooks/useTheme', () => ({
  useTheme: vi.fn(() => mockThemeValue),
  ThemeProvider: vi.fn(({ children }: { children: ReactNode }) => <ThemeContext.Provider value={mockThemeValue}>{children}</ThemeContext.Provider>),
}))

vi.mock('@/hooks/useQueries', () => ({
  useOverviewMetrics: vi.fn(() => ({
    data: { total_posts: 100, published_posts: 80, scheduled_posts: 15, connected_accounts: 3 },
    isLoading: false,
    isError: false,
  })),
  useTopPosts: vi.fn(() => ({
    data: [
      { post_id: '1', content: 'Test post 1', platform: 'twitter', likes: 50, comments: 10, shares: 5, published_at: '2024-01-15T10:00:00Z' },
      { post_id: '2', content: 'Test post 2', platform: 'linkedin', likes: 30, comments: 5, shares: 2, published_at: '2024-01-14T15:00:00Z' },
    ],
    isLoading: false,
    isError: false,
  })),
}))

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  TrendingUp: vi.fn(() => <svg data-testid="trending-icon" />),
  Users: vi.fn(() => <svg data-testid="users-icon" />),
  FileText: vi.fn(() => <svg data-testid="file-icon" />),
  Clock: vi.fn(() => <svg data-testid="clock-icon" />),
  ExternalLink: vi.fn(() => <svg data-testid="external-link-icon" />),
  Plus: vi.fn(() => <svg data-testid="plus-icon" />),
  User: vi.fn(() => <svg data-testid="user-icon" />),
  Mail: vi.fn(() => <svg data-testid="mail-icon" />),
  Lock: vi.fn(() => <svg data-testid="lock-icon" />),
  Bell: vi.fn(() => <svg data-testid="bell-icon" />),
  Shield: vi.fn(() => <svg data-testid="shield-icon" />),
  Palette: vi.fn(() => <svg data-testid="palette-icon" />),
  Database: vi.fn(() => <svg data-testid="database-icon" />),
  Trash2: vi.fn(() => <svg data-testid="trash-icon" />),
  Loader2: vi.fn(() => <svg data-testid="loader-icon" />),
  Download: vi.fn(() => <svg data-testid="download-icon" />),
}))

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useRouter: vi.fn(() => mockRouter),
  useSearchParams: vi.fn(() => mockSearchParams),
  usePathname: vi.fn(() => mockPathname),
}))

// Mock next/link
vi.mock('next/link', () => ({
  default: vi.fn(({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>{children}</a>
  )),
}))

// Mock UI components
vi.mock('@/components/ui/Button', () => ({
  Button: vi.fn(({ children, onClick, variant, size, className, isLoading, disabled, asChild, type, ...props }: any) => {
    if (asChild) {
      return <a href={props.href} onClick={onClick} className={className} data-testid="button-link">{children}</a>
    }
    return (
      <button 
        onClick={onClick} 
        disabled={disabled || isLoading}
        type={type || 'button'}
        className={className}
        data-testid={`button-${variant}-${size}`}
        {...props}
      >
        {isLoading ? 'Loading...' : children}
      </button>
    )
  }),
}))

vi.mock('@/components/ui/Input', () => ({
  Input: vi.fn(({ value, onChange, error, placeholder, disabled, className, autoComplete, autoFocus, id, name, type, ...props }: any) => (
    <input
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      disabled={disabled}
      className={className}
      autoComplete={autoComplete}
      autoFocus={autoFocus}
      id={id}
      name={name}
      type={type}
      data-testid={`input-${id || name}`}
      {...props}
    />
  )),
}))

vi.mock('@/components/ui/Label', () => ({
  Label: vi.fn(({ children, htmlFor, className, ...props }: any) => (
    <label htmlFor={htmlFor} className={className} {...props}>{children}</label>
  )),
}))

vi.mock('@/components/ui/Card', () => ({
  Card: vi.fn(({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>),
  CardHeader: vi.fn(({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>),
  CardTitle: vi.fn(({ children, className, ...props }: any) => <h3 className={className} {...props}>{children}</h3>),
  CardDescription: vi.fn(({ children, className, ...props }: any) => <p className={className} {...props}>{children}</p>),
  CardContent: vi.fn(({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>),
  CardFooter: vi.fn(({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>),
}))

vi.mock('@/components/ui/Separator', () => ({
  Separator: vi.fn(({ className, ...props }: any) => <hr className={className} {...props} />),
}))

vi.mock('@/components/ui/Avatar', () => ({
  Avatar: vi.fn(({ children, className, ...props }: any) => <div className={className} {...props} data-testid="avatar">{children}</div>),
  AvatarImage: vi.fn(({ src, alt, className, ...props }: any) => <img src={src} alt={alt} className={className} {...props} data-testid="avatar-image" />),
  AvatarFallback: vi.fn(({ children, className, ...props }: any) => <span className={className} {...props} data-testid="avatar-fallback">{children}</span>),
}))

vi.mock('@/components/ui/Badge', () => ({
  Badge: vi.fn(({ children, variant, className, ...props }: any) => (
    <span className={className} data-testid={`badge-${variant}`} {...props}>{children}</span>
  )),
}))

vi.mock('@/components/ui/Switch', () => ({
  Switch: vi.fn(({ checked, onCheckedChange, className, id, ...props }: any) => (
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => onCheckedChange(e.target.checked)}
      id={id}
      className={className}
      data-testid="switch"
      {...props}
    />
  )),
}))

vi.mock('@/components/ui/Tabs', () => ({
  Tabs: vi.fn(({ value, onValueChange, children, ...props }: any) => (
    <div data-testid="tabs" {...props}>
      {typeof children === 'function' ? children({ value, onValueChange }) : children}
    </div>
  )),
  TabsList: vi.fn(({ children, ...props }: any) => <div data-testid="tabs-list" {...props}>{children}</div>),
  TabsTrigger: vi.fn(({ value, children, ...props }: any) => (
    <button role="tab" data-value={value} data-testid="tabs-trigger" {...props}>{children}</button>
  )),
  TabsContent: vi.fn(({ value, children, ...props }: any) => (
    <div data-testid="tabs-content" data-value={value} {...props}>{children}</div>
  )),
}))

vi.mock('@/components/ui/Tooltip', () => ({
  TooltipProvider: vi.fn(({ children }: any) => <>{children}</>),
  Tooltip: vi.fn(({ children, ...props }: any) => <div {...props}>{children}</div>),
  TooltipTrigger: vi.fn(({ children, ...props }: any) => <div {...props}>{children}</div>),
  TooltipContent: vi.fn(({ children, ...props }: any) => <div {...props}>{children}</div>),
}))

vi.mock('@/components/ui/Skeleton', () => ({
  Skeleton: vi.fn(({ className, ...props }: any) => <div className={className} data-testid="skeleton" {...props} />),
}))

// Import pages after mocks
import DashboardPage from '@/app/(dashboard)/dashboard/page'
import SettingsPage from '@/app/(dashboard)/settings/page'

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <TooltipProvider>
            {children}
            <Toaster />
          </TooltipProvider>
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}

describe('Dashboard Pages', () => {
  const wrapper = createWrapper()

  beforeEach(() => {
    vi.clearAllMocks()
    mockAuthValue.user = { id: '1', name: 'Test User', email: 'test@example.com', avatar_url: null }
    mockAuthValue.isAuthenticated = true
    mockAuthValue.isLoading = false
  })

  describe('DashboardPage', () => {
    it('should render dashboard with metrics', async () => {
      render(<DashboardPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument()
      })
    })

    it('should display metrics values', async () => {
      render(<DashboardPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('100')).toBeInTheDocument() // total posts
      })
    })

    it('should render New Post button', async () => {
      render(<DashboardPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('New Post')).toBeInTheDocument()
      })
    })

    it('should render top posts section', async () => {
      render(<DashboardPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('Top Performing Posts')).toBeInTheDocument()
      })
    })

    it('should show engagement counts for top posts', async () => {
      render(<DashboardPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('65')).toBeInTheDocument() // 50 + 10 + 5 = 65
      })
    })

    it('should render quick actions', async () => {
      render(<DashboardPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('Quick Actions')).toBeInTheDocument()
      })
    })

    it('should show loading skeleton when metrics loading', async () => {
      // Mock loading state
      const { useOverviewMetrics } = await import('@/hooks/useQueries')
      vi.mocked(useOverviewMetrics).mockReturnValue({
        data: undefined,
        isLoading: true,
        isError: false,
      })

      render(<DashboardPage />, { wrapper })
      
      await waitFor(() => {
        const skeletons = screen.getAllByTestId('skeleton')
        expect(skeletons.length).toBeGreaterThan(0)
      })
    })
  })

  describe('SettingsPage', () => {
    it('should render settings page with tabs', async () => {
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument()
      })
    })

    it('should render profile tab by default', async () => {
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByText('Profile Information')).toBeInTheDocument()
      })
    })

    it('should display user info in profile tab', async () => {
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        expect(screen.getByDisplayValue('Test User')).toBeInTheDocument()
        expect(screen.getByDisplayValue('test@example.com')).toBeInTheDocument()
      })
    })

    it('should have disabled email field', async () => {
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        const emailInput = screen.getByLabelText('Email')
        expect(emailInput).toBeDisabled()
      })
    })

    it('should switch to security tab when clicked', async () => {
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        const securityTab = screen.getByRole('tab', { name: /security/i })
        fireEvent.click(securityTab)
      })
      
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /change password/i })).toBeInTheDocument()
      })
    })

    it('should call changePassword when password changed', async () => {
      const mockChangePassword = vi.fn().mockResolvedValue(true)
      const { useAuth } = await import('@/hooks/useAuth')
      vi.mocked(useAuth).mockReturnValue({
        ...mockAuthValue,
        changePassword: mockChangePassword,
      })
      
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        const securityTab = screen.getByRole('tab', { name: /security/i })
        fireEvent.click(securityTab)
      })
      
      const currentPasswordInput = screen.getByLabelText('Current Password')
      fireEvent.change(currentPasswordInput, { target: { value: 'oldpassword123' } })
      
      const newPasswordInput = screen.getByLabelText('New Password')
      fireEvent.change(newPasswordInput, { target: { value: 'newpassword123' } })
      
      const confirmPasswordInput = screen.getByLabelText('Confirm New Password')
      fireEvent.change(confirmPasswordInput, { target: { value: 'newpassword123' } })
      
      const changeButton = screen.getByRole('button', { name: /change password/i })
      fireEvent.click(changeButton)
      
      await waitFor(() => {
        expect(mockChangePassword).toHaveBeenCalledWith('oldpassword123', 'newpassword123')
      })
    })

    it('should show error when passwords do not match', async () => {
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        const securityTab = screen.getByRole('tab', { name: /security/i })
        fireEvent.click(securityTab)
      })
      
      const currentPasswordInput = screen.getByLabelText('Current Password')
      fireEvent.change(currentPasswordInput, { target: { value: 'oldpassword123' } })
      
      const newPasswordInput = screen.getByLabelText('New Password')
      fireEvent.change(newPasswordInput, { target: { value: 'newpassword123' } })
      
      const confirmPasswordInput = screen.getByLabelText('Confirm New Password')
      fireEvent.change(confirmPasswordInput, { target: { value: 'differentpassword123' } })
      
      const changeButton = screen.getByRole('button', { name: /change password/i })
      fireEvent.click(changeButton)
      
      // Should show toast error - verify changePassword wasn't called
      const { useAuth } = await import('@/hooks/useAuth')
      expect(vi.mocked(useAuth).mock.results[0].value.changePassword).not.toHaveBeenCalled()
    })

    it('should show error when new password too short', async () => {
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        const securityTab = screen.getByRole('tab', { name: /security/i })
        fireEvent.click(securityTab)
      })
      
      const currentPasswordInput = screen.getByLabelText('Current Password')
      fireEvent.change(currentPasswordInput, { target: { value: 'oldpassword123' } })
      
      const newPasswordInput = screen.getByLabelText('New Password')
      fireEvent.change(newPasswordInput, { target: { value: 'short' } })
      
      const confirmPasswordInput = screen.getByLabelText('Confirm New Password')
      fireEvent.change(confirmPasswordInput, { target: { value: 'short' } })
      
      const changeButton = screen.getByRole('button', { name: /change password/i })
      fireEvent.click(changeButton)
      
      const { useAuth } = await import('@/hooks/useAuth')
      expect(vi.mocked(useAuth).mock.results[0].value.changePassword).not.toHaveBeenCalled()
    })

    it('should call logout and redirect on delete account', async () => {
      const mockLogout = vi.fn()
      const mockPush = vi.fn()
      const { useAuth } = await import('@/hooks/useAuth')
      vi.mocked(useAuth).mockReturnValue({
        ...mockAuthValue,
        logout: mockLogout,
      })
      const { useRouter } = await import('next/navigation')
      vi.mocked(useRouter).mockReturnValue({
        push: mockPush,
        refresh: vi.fn(),
        back: vi.fn(),
      })
      
      // Mock window.confirm
      global.confirm = vi.fn(() => true)
      
      render(<SettingsPage />, { wrapper })
      
      await waitFor(() => {
        const dangerTab = screen.getByRole('tab', { name: /danger zone/i })
        fireEvent.click(dangerTab)
      })
      
      const deleteButton = screen.getByRole('button', { name: /delete account/i })
      fireEvent.click(deleteButton)
      
      await waitFor(() => {
        expect(mockLogout).toHaveBeenCalled()
        expect(mockPush).toHaveBeenCalledWith('/auth/login')
      })
    })
  })
})