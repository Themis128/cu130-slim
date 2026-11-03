import '@testing-library/jest-dom'
import { vi } from 'vitest'

// Global localStorage mock for all tests. The Map-backed implementation lives
// on Storage.prototype itself so that vi.spyOn(Storage.prototype, 'setItem')
// (used e.g. in useNotifications.test.tsx) wraps the real implementation and
// captures calls made through globalThis.localStorage.
// jsdom-only: guarded so node-environment test files (no DOM) skip it.
if (typeof Storage !== 'undefined') {
  const lsStore = new Map<string, string>()

  const defineProto = (name: string, fn: (...args: never[]) => unknown) => {
    Object.defineProperty(Storage.prototype, name, {
      value: fn,
      writable: true,
      configurable: true,
    })
  }

  defineProto('getItem', function (key: string) {
    return lsStore.has(key) ? lsStore.get(key)! : null
  })
  defineProto('setItem', function (key: string, value: string) {
    lsStore.set(String(key), String(value))
  })
  defineProto('removeItem', function (key: string) {
    lsStore.delete(key)
  })
  defineProto('clear', function () {
    lsStore.clear()
  })
  defineProto('key', function (index: number) {
    return Array.from(lsStore.keys())[index] ?? null
  })

  const localStorageMock = Object.create(Storage.prototype) as Storage
  Object.defineProperty(localStorageMock, 'length', {
    get: () => lsStore.size,
    configurable: true,
  })

  Object.defineProperty(globalThis, 'localStorage', {
    value: localStorageMock,
    writable: true,
    configurable: true,
  })
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('next/link', () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string } & Record<string, unknown>) => {
    return <a href={href} {...rest}>{children}</a>
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

// Canvas stubs only exist under jsdom; node-environment test files
// (e.g. services/api.ssr.test.ts) run without a DOM.
if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      fillRect: vi.fn(),
      clearRect: vi.fn(),
      getImageData: vi.fn(() => ({ data: [] })),
      putImageData: vi.fn(),
      createImageData: vi.fn(() => ({ data: [] })),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      closePath: vi.fn(),
      stroke: vi.fn(),
      fill: vi.fn(),
    }))
  HTMLCanvasElement.prototype.toDataURL = vi.fn(() => 'data:image/png;base64,')
}
