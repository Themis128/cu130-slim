import '@testing-library/jest-dom'
import { vi } from 'vitest'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => {
    return <a href={href}>{children}</a>
  },
}))

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
    loading: vi.fn(),
    dismiss: vi.fn(),
  },
  Toaster: () => null,
}))

HTMLCanvasElement.prototype.getContext = vi.fn()
HTMLCanvasElement.prototype.toDataURL = vi.fn(() => 'data:image/png;base64,')