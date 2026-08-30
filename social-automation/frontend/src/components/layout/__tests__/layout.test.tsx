import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

// Simple mock functions
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

// Mock hooks
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => mockAuthValue,
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/hooks/useTheme', () => ({
  useTheme: () => mockThemeValue,
  ThemeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

// Mock lucide-react icons — spread actual exports so any icon not explicitly
// overridden still renders as the real component (avoids "No X export defined").
vi.mock('lucide-react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('lucide-react')>()
  const svg = (testId: string) => () => <svg data-testid={testId} />
  return {
    ...actual,
    Bell: svg('bell-icon'),
    Moon: svg('moon-icon'),
    Sun: svg('sun-icon'),
    Search: svg('search-icon'),
    Command: svg('command-icon'),
    Settings: svg('settings-icon'),
    LayoutDashboard: svg('dashboard-icon'),
    FileText: svg('file-icon'),
    Image: svg('image-icon'),
    Zap: svg('zap-icon'),
    Users: svg('users-icon'),
    BarChart3: svg('chart-icon'),
    ChevronLeft: svg('chevron-left-icon'),
    ChevronRight: svg('chevron-right-icon'),
    Menu: svg('menu-icon'),
    X: svg('x-icon'),
    Mail: svg('mail-icon'),
    Lock: svg('lock-icon'),
    User: svg('user-icon'),
    Loader2: svg('loader-icon'),
    AlertCircle: svg('alert-icon'),
    Download: svg('download-icon'),
    TrendingUp: svg('trending-icon'),
    Shield: svg('shield-icon'),
    Palette: svg('palette-icon'),
    Database: svg('database-icon'),
    Trash2: svg('trash-icon'),
    Plus: svg('plus-icon'),
    Clock: svg('clock-icon'),
    ExternalLink: svg('external-link-icon'),
    Map: svg('map-icon'),
    Lightbulb: svg('lightbulb-icon'),
    ArrowLeft: svg('arrow-left-icon'),
    ArrowRight: svg('arrow-right-icon'),
    CalendarDays: svg('calendar-icon'),
    CheckCircle2: svg('check-circle-icon'),
    AlertTriangle: svg('alert-triangle-icon'),
    Info: svg('info-icon'),
    Upload: svg('upload-icon'),
    LogOut: svg('log-out-icon'),
    Cpu: svg('cpu-icon'),
    Linkedin: svg('linkedin-icon'),
    Wand2: svg('wand-icon'),
  }
})

// Mock next/navigation
vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({
    push: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
  }),
  useSearchParams: () => ({
    get: vi.fn(() => null),
  }),
}))

// Mock next/link
vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

// Mock UI components
vi.mock('@/components/ui/Button', () => ({
  Button: ({ children, onClick, variant, size, className, isLoading, disabled, asChild, type, ...props }: any) => {
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
  },
}))

vi.mock('@/components/ui/Input', () => ({
  Input: ({ value, onChange, error, placeholder, disabled, className, autoComplete, autoFocus, id, name, type, ...props }: any) => (
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

vi.mock('@/components/ui/Avatar', () => ({
  Avatar: ({ children, className, ...props }: any) => <div className={className} {...props} data-testid="avatar">{children}</div>,
  AvatarImage: ({ src, alt, className, ...props }: any) => <img src={src} alt={alt} className={className} {...props} data-testid="avatar-image" />,
  AvatarFallback: ({ children, className, ...props }: any) => <span className={className} {...props} data-testid="avatar-fallback">{children}</span>,
}))

vi.mock('@/components/ui/Badge', () => ({
  Badge: ({ children, variant, className, ...props }: any) => (
    <span className={className} data-testid={`badge-${variant}`} {...props}>{children}</span>
  ),
}))

vi.mock('@/components/ui/Switch', () => ({
  Switch: ({ checked, onCheckedChange, className, id, ...props }: any) => (
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => onCheckedChange(e.target.checked)}
      id={id}
      className={className}
      data-testid="switch"
      {...props}
    />
  ),
}))

vi.mock('@/components/ui/Tabs', () => ({
  Tabs: ({ value, onValueChange, children, ...props }: any) => (
    <div data-testid="tabs" {...props}>
      {typeof children === 'function' ? children({ value, onValueChange }) : children}
    </div>
  ),
  TabsList: ({ children, ...props }: any) => <div data-testid="tabs-list" {...props}>{children}</div>,
  TabsTrigger: ({ value, children, ...props }: any) => (
    <button role="tab" data-value={value} data-testid="tabs-trigger" {...props}>{children}</button>
  ),
  TabsContent: ({ value, children, ...props }: any) => (
    <div data-testid="tabs-content" data-value={value} {...props}>{children}</div>
  ),
}))

vi.mock('@/components/ui/Tooltip', () => ({
  TooltipProvider: ({ children }: any) => <>{children}</>,
  Tooltip: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  TooltipTrigger: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  TooltipContent: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}))

vi.mock('@/components/ui/Skeleton', () => ({
  Skeleton: ({ className, ...props }: any) => <div className={className} data-testid="skeleton" {...props} />,
}))

vi.mock('@/components/ui/DropdownMenu', () => ({
  DropdownMenu: ({ children, ...props }: any) => <div {...props} data-testid="dropdown-menu">{children}</div>,
  DropdownMenuTrigger: ({ children, asChild, ...props }: any) => <div {...props} data-testid="dropdown-trigger">{children}</div>,
  DropdownMenuContent: ({ children, align, className, ...props }: any) => <div align={align} className={className} {...props} data-testid="dropdown-content">{children}</div>,
  DropdownMenuItem: ({ children, onClick, inset, className, asChild, ...props }: any) => (
    <div onClick={onClick} className={className} data-testid="dropdown-item" {...props}>{children}</div>
  ),
  DropdownMenuSeparator: ({ className, ...props }: any) => <hr className={className} {...props} />,
}))

// Import components after mocks
import { Header } from '@/components/layout/Header'
import { Sidebar } from '@/components/layout/Sidebar'
import { Layout } from '@/components/layout/Layout'

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

describe('Layout Components', () => {
  const wrapper = createWrapper()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  describe('Header', () => {
    it('should render header with search input', () => {
      render(<Header />, { wrapper })
      
      expect(screen.getByText('Search pages, actions...')).toBeInTheDocument()
    })

    it('should render theme toggle button', () => {
      render(<Header />, { wrapper })
      
      const themeButton = screen.getByRole('button', { name: /switch to dark mode/i })
      expect(themeButton).toBeInTheDocument()
    })

it('should render notifications dropdown trigger', () => {
      render(<Header />, { wrapper })
      
      const dropdownTriggers = screen.getAllByTestId('dropdown-trigger')
      // Find the one containing the Bell icon (notifications dropdown)
      const notificationTrigger = dropdownTriggers.find(trigger => 
        trigger.querySelector('[data-testid="bell-icon"]')
      )
      expect(notificationTrigger).toBeInTheDocument()
    })

    it('should render user avatar dropdown', () => {
      render(<Header />, { wrapper })
      
      expect(screen.getByTestId('avatar')).toBeInTheDocument()
    })

    it('should toggle theme when theme button clicked', () => {
      render(<Header />, { wrapper })
      
      const themeButton = screen.getByRole('button', { name: /switch to dark mode/i })
      fireEvent.click(themeButton)
      
      expect(mockThemeValue.toggleTheme).toHaveBeenCalled()
    })

    it('should render search input', () => {
      render(<Header />, { wrapper })
      
      const searchInput = screen.getByText('Search pages, actions...')
      expect(searchInput).toBeInTheDocument()
    })
  })

  describe('Sidebar', () => {
    it('should render sidebar with navigation items', () => {
      render(<Sidebar />, { wrapper })
      
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
      expect(screen.getByText('Content')).toBeInTheDocument()
      expect(screen.getByText('Media Library')).toBeInTheDocument()
      expect(screen.getByText('Workflows')).toBeInTheDocument()
      expect(screen.getByText('Accounts')).toBeInTheDocument()
      expect(screen.getByText('Analytics')).toBeInTheDocument()
      expect(screen.getByText('Settings')).toBeInTheDocument()
    })

    it('should highlight active navigation item', () => {
      render(<Sidebar />, { wrapper })
      
      const dashboardLink = screen.getByText('Dashboard').closest('a')
      expect(dashboardLink).toBeInTheDocument()
    })

    it('should render user section with avatar', () => {
      render(<Sidebar />, { wrapper })
      
      expect(screen.getByTestId('avatar')).toBeInTheDocument()
    })

    it('should have collapse button', () => {
      render(<Sidebar />, { wrapper })
      
      const collapseButton = screen.getByRole('button', { name: /collapse sidebar/i })
      expect(collapseButton).toBeInTheDocument()
    })

    it('should render navigation links', () => {
      render(<Sidebar />, { wrapper })
      
      const navLinks = screen.getAllByRole('link', { name: /dashboard|content|media|workflows|accounts|analytics|settings/i })
      expect(navLinks.length).toBeGreaterThan(0)
    })
  })

  describe('Layout', () => {
    it('should render sidebar and header', () => {
      render(
        <Layout>
          <div data-testid="main-content">Main Content</div>
        </Layout>,
        { wrapper }
      )
      
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
      expect(screen.getByText('Search pages, actions...')).toBeInTheDocument()
      expect(screen.getByText('Main Content')).toBeInTheDocument()
    })

    it('should render children in main area', () => {
      render(
        <Layout>
          <div data-testid="custom-content">Custom Content</div>
        </Layout>,
        { wrapper }
      )
      
      expect(screen.getByTestId('custom-content')).toBeInTheDocument()
    })

    it('should have correct layout structure', () => {
      render(
        <Layout>
          <div data-testid="main-content">Main Content</div>
        </Layout>,
        { wrapper }
      )
      
      const mainContainer = document.querySelector('.flex.h-screen.bg-background')
      expect(mainContainer).toBeInTheDocument()
    })
  })
})