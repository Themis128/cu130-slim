import { renderHook, act, waitFor } from '@testing-library/react'
import { ThemeProvider, useTheme } from '@/hooks/useTheme'
import { ReactNode } from 'react'

const originalMatchMedia = window.matchMedia

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  window.matchMedia = vi.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
})

afterEach(() => {
  window.matchMedia = originalMatchMedia
})

const wrapper = ({ children }: { children: ReactNode }) => <ThemeProvider>{children}</ThemeProvider>

describe('useTheme', () => {
  it('initializes with system theme by default (no localStorage)', async () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => {
      expect(result.current.theme).toBe('system')
      expect(result.current.resolvedTheme).toBe('light')
    })
  })

  it('reads theme from localStorage', async () => {
    localStorage.setItem('theme', 'dark')
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => {
      expect(result.current.theme).toBe('dark')
      expect(result.current.resolvedTheme).toBe('dark')
    })
  })

  it('reads system theme from localStorage', async () => {
    localStorage.setItem('theme', 'system')
    window.matchMedia = vi.fn().mockImplementation(query => ({
      matches: true,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => {
      expect(result.current.theme).toBe('system')
      expect(result.current.resolvedTheme).toBe('dark')
    })
  })

  it('toggles theme between light and dark', async () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => expect(result.current.theme).toBe('system'))

    act(() => {
      result.current.toggleTheme()
    })

    await waitFor(() => {
      expect(result.current.theme).toBe('dark')
      expect(localStorage.getItem('theme')).toBe('dark')
    })
  })

  it('sets specific theme', async () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => expect(result.current.theme).toBe('system'))

    act(() => {
      result.current.setTheme('dark')
    })

    await waitFor(() => {
      expect(result.current.theme).toBe('dark')
      expect(localStorage.getItem('theme')).toBe('dark')
    })
  })

  it('sets system theme', async () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => expect(result.current.theme).toBe('system'))

    act(() => {
      result.current.setTheme('system')
    })

    await waitFor(() => {
      expect(result.current.theme).toBe('system')
      expect(localStorage.getItem('theme')).toBe('system')
    })
  })

  it('applies dark class to document when resolved theme is dark', async () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(false))

    act(() => {
      result.current.setTheme('dark')
    })

    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(true))
  })

  it('removes dark class when resolved theme is light', async () => {
    const { result, rerender } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(false))

    document.documentElement.classList.add('dark')

    act(() => {
      result.current.setTheme('light')
    })

    rerender()
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(false))
  })

  it('listens to system theme changes when theme is system', async () => {
    let mediaQueryListener: (event: MediaQueryListEvent) => void
    window.matchMedia = vi.fn().mockImplementation(query => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn((listener) => { mediaQueryListener = listener }),
      removeListener: vi.fn(),
      addEventListener: vi.fn((_, listener) => { mediaQueryListener = listener }),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))

    localStorage.setItem('theme', 'system')
    const { result } = renderHook(() => useTheme(), { wrapper })
    await waitFor(() => expect(result.current.resolvedTheme).toBe('light'))

    act(() => {
      mediaQueryListener!({ matches: true } as MediaQueryListEvent)
    })

    await waitFor(() => expect(result.current.resolvedTheme).toBe('dark'))
  })
})