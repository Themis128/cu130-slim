import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

// Simple mock functions
const mockAuthValue = {
  user: null,
  login: vi.fn().mockResolvedValue(true),
  register: vi.fn().mockResolvedValue(true),
  logout: vi.fn(),
  refreshUser: vi.fn(),
  updateProfile: vi.fn().mockResolvedValue(true),
  changePassword: vi.fn().mockResolvedValue(true),
  isAuthenticated: false,
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

// Mock hooks
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => mockAuthValue,
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/hooks/useTheme', () => ({
  useTheme: () => mockThemeValue,
  ThemeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => mockSearchParams,
  usePathname: () => '/dashboard',
}))

// Mock next/link
vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  Mail: () => <svg data-testid="mail-icon" />,
  Lock: () => <svg data-testid="lock-icon" />,
  User: () => <svg data-testid="user-icon" />,
  Loader2: () => <svg data-testid="loader-icon" />,
  AlertCircle: () => <svg data-testid="alert-icon" />,
}))

// Mock UI components
vi.mock('@/components/ui/Button', () => ({
  Button: ({ children, onClick, variant, size, className, isLoading, disabled, type, ...props }: any) => (
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
  ),
}))

vi.mock('@/components/ui/Input', () => ({
  Input: ({ value, onChange, error, placeholder, disabled, className, autoComplete, autoFocus, id, name, type, ...props }: any) => (
    <div>
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
      {error && <p data-testid={`input-error-${id || name}`} className="text-sm text-destructive">{error}</p>}
    </div>
  ),
}))

vi.mock('@/components/ui/Label', () => ({
  Label: ({ children, htmlFor, className, ...props }: any) => (
    <label htmlFor={htmlFor} className={className} {...props}>{children}</label>
  ),
}))

vi.mock('@/components/ui/Card', () => ({
  Card: ({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>,
  CardHeader: ({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>,
  CardTitle: ({ children, className, ...props }: any) => <h3 className={className} {...props}>{children}</h3>,
  CardDescription: ({ children, className, ...props }: any) => <p className={className} {...props}>{children}</p>,
  CardContent: ({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>,
  CardFooter: ({ children, className, ...props }: any) => <div className={className} {...props}>{children}</div>,
}))

vi.mock('@/components/ui/Separator', () => ({
  Separator: ({ className, ...props }: any) => <hr className={className} {...props} />,
}))

vi.mock('@/components/ui/Tooltip', () => ({
  TooltipProvider: ({ children }: any) => <>{children}</>,
}))

// Import pages after mocks
import LoginPage from '@/app/(auth)/login/page'
import RegisterPage from '@/app/(auth)/register/page'

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  )
}

describe('Auth Pages', () => {
  const wrapper = createWrapper()

  beforeEach(() => {
    vi.clearAllMocks()
    mockSearchParams.get.mockReturnValue(null)
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  describe('LoginPage', () => {
    it('should render login form', () => {
      render(<LoginPage />, { wrapper })
      
      expect(screen.getByText('Welcome back')).toBeInTheDocument()
      expect(screen.getByText('Sign in to your account to continue')).toBeInTheDocument()
      expect(screen.getByLabelText('Email')).toBeInTheDocument()
      expect(screen.getByLabelText('Password')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    })

    it('should show email and password fields', () => {
      render(<LoginPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      const passwordInput = screen.getByLabelText('Password')
      
      expect(emailInput).toBeInTheDocument()
      expect(passwordInput).toBeInTheDocument()
      expect(emailInput).toHaveAttribute('type', 'email')
      expect(passwordInput).toHaveAttribute('type', 'password')
    })

    it('should show forgot password link', () => {
      render(<LoginPage />, { wrapper })
      
      expect(screen.getByText('Forgot password?')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /forgot password/i })).toHaveAttribute('href', '/auth/forgot-password')
    })

    it('should show sign up link', () => {
      render(<LoginPage />, { wrapper })
      
      expect(screen.getByText("Don't have an account?")).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /sign up/i })).toHaveAttribute('href', '/auth/register')
    })

    it('should validate email field', async () => {
      render(<LoginPage />, { wrapper })
      
      const submitButton = screen.getByRole('button', { name: /sign in/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByText('Email is required')).toBeInTheDocument()
      })
    })

    it('should validate password field', async () => {
      render(<LoginPage />, { wrapper })
      
      const submitButton = screen.getByRole('button', { name: /sign in/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByText('Password is required')).toBeInTheDocument()
      })
    })

    it('should validate email format', async () => {
      render(<LoginPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'invalid-email' } })
      
      const form = screen.getByTestId('input-email').closest('form')
      fireEvent.submit(form)
      
      await waitFor(() => {
        const errorElement = screen.getByTestId('input-error-email')
        expect(errorElement).toBeInTheDocument()
        expect(errorElement).toHaveTextContent('Invalid email address')
      })
    })

    it('should validate password length', async () => {
      render(<LoginPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'short' } })
      
      const submitButton = screen.getByRole('button', { name: /sign in/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByTestId('input-error-password')).toHaveTextContent('Password must be at least 8 characters')
      })
    })

    it('should call login on valid form submit', async () => {
      render(<LoginPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'password123' } })
      
      const submitButton = screen.getByRole('button', { name: /sign in/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(mockAuthValue.login).toHaveBeenCalledWith({ email: 'test@example.com', password: 'password123' })
      })
    })

    it('should show loading state during login', async () => {
      let resolveLogin: (value: boolean) => void
      const loginPromise = new Promise<boolean>((resolve) => { resolveLogin = resolve })
      mockAuthValue.login = vi.fn(() => loginPromise)
      
      render(<LoginPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'password123' } })
      
      const submitButton = screen.getByRole('button', { name: /sign in/i })
      fireEvent.click(submitButton)
      
      expect(screen.getByText('Loading...')).toBeInTheDocument()
      expect(submitButton).toBeDisabled()
      
      resolveLogin!(true)
      await waitFor(() => {
        expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
      })
    })

    it('should redirect on successful login', async () => {
      mockAuthValue.login = vi.fn().mockResolvedValue(true)
      
      render(<LoginPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'password123' } })
      
      const submitButton = screen.getByRole('button', { name: /sign in/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(mockRouter.push).toHaveBeenCalledWith('/dashboard')
      })
    })

    it('should use callbackUrl from search params', () => {
      mockSearchParams.get.mockReturnValue('/custom-callback')
      
      render(<LoginPage />, { wrapper })
      
      expect(screen.getByText('Welcome back')).toBeInTheDocument()
    })
  })

  describe('RegisterPage', () => {
    it('should render register form', () => {
      render(<RegisterPage />, { wrapper })
      
      expect(screen.getByText('Create your account')).toBeInTheDocument()
      expect(screen.getByText('Start automating your social media today')).toBeInTheDocument()
      expect(screen.getByLabelText('Full Name')).toBeInTheDocument()
      expect(screen.getByLabelText('Email')).toBeInTheDocument()
      expect(screen.getByLabelText('Password')).toBeInTheDocument()
      expect(screen.getByLabelText('Confirm Password')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /create account/i })).toBeInTheDocument()
    })

    it('should show all form fields with correct types', () => {
      render(<RegisterPage />, { wrapper })
      
      expect(screen.getByLabelText('Full Name')).toHaveAttribute('type', 'text')
      expect(screen.getByLabelText('Email')).toHaveAttribute('type', 'email')
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
      expect(screen.getByLabelText('Confirm Password')).toHaveAttribute('type', 'password')
    })

    it('should show sign in link', () => {
      render(<RegisterPage />, { wrapper })
      
      expect(screen.getByText('Already have an account?')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/auth/login')
    })

    it('should validate full name field', async () => {
      render(<RegisterPage />, { wrapper })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByText('Full name is required')).toBeInTheDocument()
      })
    })

    it('should validate email field', async () => {
      render(<RegisterPage />, { wrapper })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByText('Email is required')).toBeInTheDocument()
      })
    })

    it('should validate email format', async () => {
      render(<RegisterPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'invalid-email' } })
      
      const form = screen.getByTestId('input-email').closest('form')
      fireEvent.submit(form)
      
      await waitFor(() => {
        const errorElement = screen.getByTestId('input-error-email')
        expect(errorElement).toBeInTheDocument()
        expect(errorElement).toHaveTextContent('Invalid email address')
      })
    })

    it('should validate password length', async () => {
      render(<RegisterPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'short' } })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByTestId('input-error-password')).toHaveTextContent('Password must be at least 8 characters')
      })
    })

    it('should validate password confirmation match', async () => {
      render(<RegisterPage />, { wrapper })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'password123' } })
      
      const confirmInput = screen.getByLabelText('Confirm Password')
      fireEvent.change(confirmInput, { target: { value: 'different123' } })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByTestId('input-error-confirmPassword')).toHaveTextContent('Passwords do not match')
      })
    })

    it('should call register on valid form submit', async () => {
      render(<RegisterPage />, { wrapper })
      
      const nameInput = screen.getByLabelText('Full Name')
      fireEvent.change(nameInput, { target: { value: 'Test User' } })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'password123' } })
      
      const confirmInput = screen.getByLabelText('Confirm Password')
      fireEvent.change(confirmInput, { target: { value: 'password123' } })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(mockAuthValue.register).toHaveBeenCalledWith({
          name: 'Test User',
          email: 'test@example.com',
          password: 'password123',
        })
      })
    })

    it('should show loading state during registration', async () => {
      let resolveRegister: (value: boolean) => void
      const registerPromise = new Promise<boolean>((resolve) => { resolveRegister = resolve })
      mockAuthValue.register = vi.fn(() => registerPromise)
      
      render(<RegisterPage />, { wrapper })
      
      const nameInput = screen.getByLabelText('Full Name')
      fireEvent.change(nameInput, { target: { value: 'Test User' } })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'password123' } })
      
      const confirmInput = screen.getByLabelText('Confirm Password')
      fireEvent.change(confirmInput, { target: { value: 'password123' } })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      expect(screen.getByText('Loading...')).toBeInTheDocument()
      expect(submitButton).toBeDisabled()
      
      resolveRegister!(true)
      await waitFor(() => {
        expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
      })
    })

    it('should redirect on successful registration', async () => {
      mockAuthValue.register = vi.fn().mockResolvedValue(true)
      
      render(<RegisterPage />, { wrapper })
      
      const nameInput = screen.getByLabelText('Full Name')
      fireEvent.change(nameInput, { target: { value: 'Test User' } })
      
      const emailInput = screen.getByLabelText('Email')
      fireEvent.change(emailInput, { target: { value: 'test@example.com' } })
      
      const passwordInput = screen.getByLabelText('Password')
      fireEvent.change(passwordInput, { target: { value: 'password123' } })
      
      const confirmInput = screen.getByLabelText('Confirm Password')
      fireEvent.change(confirmInput, { target: { value: 'password123' } })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(mockRouter.push).toHaveBeenCalledWith('/dashboard')
      })
    })

    it('should clear error when user types in field', async () => {
      render(<RegisterPage />, { wrapper })
      
      const submitButton = screen.getByRole('button', { name: /create account/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(screen.getByTestId('input-error-full_name')).toHaveTextContent('Full name is required')
      })
      
      const nameInput = screen.getByLabelText('Full Name')
      fireEvent.change(nameInput, { target: { value: 'Test' } })
      
      await waitFor(() => {
        expect(screen.queryByTestId('input-error-full_name')).not.toBeInTheDocument()
      })
    })
  })
})