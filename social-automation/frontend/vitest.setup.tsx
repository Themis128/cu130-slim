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

vi.mock('@/services/api', () => ({
  authApi: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    refresh: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
  },
  postsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    publish: vi.fn(),
    schedule: vi.fn(),
  },
  mediaApi: {
    list: vi.fn(),
    upload: vi.fn(),
    delete: vi.fn(),
    generateImage: vi.fn(),
  },
  workflowsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    execute: vi.fn(),
    listTemplates: vi.fn(),
  },
  accountsApi: {
    list: vi.fn(),
    connect: vi.fn(),
    disconnect: vi.fn(),
    getAuthUrl: vi.fn(),
  },
  analyticsApi: {
    getOverview: vi.fn(),
    getPostAnalytics: vi.fn(),
    getAccountAnalytics: vi.fn(),
  },
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
    interceptors: {
      request: { use: vi.fn(), eject: vi.fn() },
      response: { use: vi.fn(), eject: vi.fn() },
    },
  },
  setTokens: vi.fn(),
  clearTokens: vi.fn(),
  getAccessToken: vi.fn(),
  getRefreshToken: vi.fn(),
}))


vi.mock('@/hooks/useQueries', () => ({
  usePosts: () => ({ data: [], isLoading: false, error: null }),
  usePost: () => ({ data: null, isLoading: false, error: null }),
  useCreatePost: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdatePost: () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePost: () => ({ mutate: vi.fn(), isPending: false }),
  usePublishPost: () => ({ mutate: vi.fn(), isPending: false }),
  useSchedulePost: () => ({ mutate: vi.fn(), isPending: false }),
  useMedia: () => ({ data: [], isLoading: false, error: null }),
  useUploadMedia: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteMedia: () => ({ mutate: vi.fn(), isPending: false }),
  useGenerateImage: () => ({ mutate: vi.fn(), isPending: false }),
  useWorkflows: () => ({ data: [], isLoading: false, error: null }),
  useWorkflow: () => ({ data: null, isLoading: false, error: null }),
  useCreateWorkflow: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateWorkflow: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteWorkflow: () => ({ mutate: vi.fn(), isPending: false }),
  useExecuteWorkflow: () => ({ mutate: vi.fn(), isPending: false }),
  useWorkflowTemplates: () => ({ data: [], isLoading: false, error: null }),
  useSocialAccounts: () => ({ data: [], isLoading: false, error: null }),
  useConnectAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useDisconnectAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useAccountAuthUrl: () => ({ data: null, isLoading: false, error: null }),
  useAnalyticsOverview: () => ({ data: null, isLoading: false, error: null }),
  usePostAnalytics: () => ({ data: null, isLoading: false, error: null }),
  useAccountAnalytics: () => ({ data: null, isLoading: false, error: null }),
}))

HTMLCanvasElement.prototype.getContext = vi.fn()
HTMLCanvasElement.prototype.toDataURL = vi.fn(() => 'data:image/png;base64,')