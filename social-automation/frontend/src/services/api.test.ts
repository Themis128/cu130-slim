import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import axios from 'axios'

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
}

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
})

// Mock window.location
const originalLocation = window.location
delete window.location
window.location = { href: '' } as Location

// Mock axios
vi.mock('axios')
const mockedAxios = vi.mocked(axios)

describe('API Service', () => {
  let apiModule: any
  let mockAxiosInstance: any
  let mockRequestInterceptor: any
  let mockResponseInterceptor: any
  let mockResponseFulfilled: any

  beforeEach(async () => {
    vi.resetModules()
    vi.clearAllMocks()
    localStorageMock.getItem.mockReturnValue(null)
    
    // Setup axios mock - create a callable function that acts as the axios instance
    mockRequestInterceptor = vi.fn((config) => config)
    mockResponseInterceptor = vi.fn((response) => response)
    
    // Create a callable mock axios instance
    const mockCall = vi.fn()
    mockAxiosInstance = Object.assign(mockCall, {
      defaults: { baseURL: '/api/v1', headers: { 'Content-Type': 'application/json' } },
      interceptors: {
        request: { 
          use: vi.fn().mockImplementation((fn) => { 
            console.log('Request interceptor registered:', fn)
            mockRequestInterceptor = fn 
          }) 
        },
        response: { 
          use: vi.fn().mockImplementation((onFulfilled, onRejected) => { 
            console.log('Response interceptor registered:', { onFulfilled, onRejected })
            mockResponseInterceptor = onRejected // Capture the error handler (second arg)
            mockResponseFulfilled = onFulfilled // Capture the success handler (first arg)
          }) 
        },
      },
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      put: vi.fn(),
    })
    
    mockedAxios.create.mockReturnValue(mockAxiosInstance)
    mockedAxios.post.mockResolvedValue({ data: { access_token: 'new-access', refresh_token: 'new-refresh' } })
    mockedAxios.isAxiosError.mockReturnValue(false)
    
    // Import the module
    apiModule = await import('@/services/api')
  })

  afterEach(() => {
    vi.resetAllMocks()
    window.location.href = ''
  })

  describe('axios instance creation', () => {
    it('should create axios instance with correct baseURL', () => {
      expect(mockedAxios.create).toHaveBeenCalledWith({
        baseURL: '/api/v1',
        headers: { 'Content-Type': 'application/json' },
      })
    })

    it('should export the axios instance as default', () => {
      expect(apiModule.default).toBeDefined()
      expect(apiModule.default.defaults.baseURL).toBe('/api/v1')
    })

    it('should export all API modules', () => {
      expect(apiModule.authApi).toBeDefined()
      expect(apiModule.contentApi).toBeDefined()
      expect(apiModule.mediaApi).toBeDefined()
      expect(apiModule.workflowApi).toBeDefined()
      expect(apiModule.accountsApi).toBeDefined()
      expect(apiModule.publishingApi).toBeDefined()
      expect(apiModule.analyticsApi).toBeDefined()
      expect(apiModule.aiApi).toBeDefined()
    })
  })

  describe('Token Management', () => {
    it('should set tokens in memory and localStorage', () => {
      apiModule.setTokens('access-token', 'refresh-token')
      
      expect(localStorageMock.setItem).toHaveBeenCalledWith('access_token', 'access-token')
      expect(localStorageMock.setItem).toHaveBeenCalledWith('refresh_token', 'refresh-token')
    })

    it('should clear tokens from memory and localStorage', () => {
      apiModule.setTokens('access-token', 'refresh-token')
      apiModule.clearTokens()
      
      expect(localStorageMock.removeItem).toHaveBeenCalledWith('access_token')
      expect(localStorageMock.removeItem).toHaveBeenCalledWith('refresh_token')
    })

    it('should get access token from localStorage when in browser', () => {
      localStorageMock.getItem.mockReturnValue('stored-access-token')
      const token = apiModule.getAccessToken()
      
      expect(token).toBe('stored-access-token')
      expect(localStorageMock.getItem).toHaveBeenCalledWith('access_token')
    })

    it('should get refresh token from localStorage when in browser', () => {
      localStorageMock.getItem.mockReturnValue('stored-refresh-token')
      const token = apiModule.getRefreshToken()
      
      expect(token).toBe('stored-refresh-token')
      expect(localStorageMock.getItem).toHaveBeenCalledWith('refresh_token')
    })

    it('should return in-memory access token when not in browser', () => {
      // Simulate SSR by removing window
      const originalWindow = global.window
      // @ts-ignore
      delete global.window
      
      apiModule.setTokens('memory-access', 'memory-refresh')
      const token = apiModule.getAccessToken()
      
      expect(token).toBe('memory-access')
      
      // @ts-ignore
      global.window = originalWindow
    })

    it('should logout and redirect to login', () => {
      apiModule.setTokens('access-token', 'refresh-token')
      apiModule.logout()
      
      expect(localStorageMock.removeItem).toHaveBeenCalledWith('access_token')
      expect(localStorageMock.removeItem).toHaveBeenCalledWith('refresh_token')
      expect(window.location.href).toBe('/login')
    })
  })

  describe('Request Interceptor', () => {
    it('should add Authorization header when access token exists', () => {
      const config = { headers: {} }
      apiModule.setTokens('test-access-token', 'test-refresh-token')
      
      const result = mockRequestInterceptor(config)
      
      expect(result.headers.Authorization).toBe('Bearer test-access-token')
    })

    it('should not add Authorization header when no access token', () => {
      const config = { headers: {} }
      apiModule.clearTokens()
      
      const result = mockRequestInterceptor(config)
      
      expect(result.headers.Authorization).toBeUndefined()
    })

    it('should reject on request error', () => {
      const error = new Error('Request failed')
      const result = mockRequestInterceptor(Promise.reject(error))
      
      return expect(result).rejects.toThrow('Request failed')
    })
  })

  describe('Response Interceptor - Token Refresh', () => {
    it('should return response as-is for successful requests', () => {
      const response = { data: { success: true } }
      const result = mockResponseFulfilled(response)
      
      expect(result).toBe(response)
    })

    it('should attempt token refresh on 401 error', async () => {
      const originalRequest = { 
        headers: {}, 
        _retry: false,
        url: '/test'
      }
      const error = {
        response: { status: 401 },
        config: originalRequest,
      }
      
      apiModule.setTokens('old-access', 'old-refresh')
      
      const promise = mockResponseInterceptor(error)
      
      // Wait for the async interceptor to complete
      await promise
      
      expect(mockedAxios.post).toHaveBeenCalledWith('/api/v1/auth/refresh', {
        refresh_token: 'old-refresh',
      })
    })

    it('should queue requests while refreshing', async () => {
      const originalRequest = { headers: {}, _retry: false }
      const error = { response: { status: 401 }, config: originalRequest }
      
      apiModule.setTokens('old-access', 'old-refresh')
      
      // Track all axios instance calls
      const calls: any[] = []
      mockAxiosInstance.mockImplementation((...args) => {
        calls.push(args)
        console.log('mockAxiosInstance called with:', args)
        const result = Promise.resolve({ data: { success: true } })
        console.log('mockAxiosInstance returning:', result)
        return result
      })
      
      // Also track the onFulfilled handler
      let onFulfilledCalls = 0
      const originalOnFulfilled = mockResponseFulfilled
      mockResponseFulfilled = (...args: any[]) => {
        onFulfilledCalls++
        console.log('onFulfilled called', onFulfilledCalls, 'with:', args)
        return originalOnFulfilled?.(...args)
      }
      
      // Start first refresh
      const promise1 = mockResponseInterceptor(error)
      
      // Second request should be queued
      const promise2 = mockResponseInterceptor({ ...error, config: { ...originalRequest, url: '/test2' } })
      
      // Wait for both promises to complete
      console.log('About to await Promise.all')
      const [result1, result2] = await Promise.all([
        promise1.catch(e => { console.log('Promise1 rejected:', e); throw e }),
        promise2.catch(e => { console.log('Promise2 rejected:', e); throw e })
      ])
      console.log('Promise.all completed')
      
      console.log('Result1:', result1)
      console.log('Result2:', result2)
      console.log('All axios calls:', calls)
      console.log('onFulfilled calls:', onFulfilledCalls)
      
      // Only one refresh call should be made
      expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    })

    it('should attempt logout on refresh failure', async () => {
      const originalRequest = { headers: {}, _retry: false, url: '/test' }
      const error = { response: { status: 401 }, config: originalRequest }
      
      mockedAxios.post.mockRejectedValueOnce(new Error('Refresh failed'))
      apiModule.setTokens('old-access', 'old-refresh')
      
      // Await the promise to allow async interceptor to run
      // The interceptor will call logout() which sets window.location.href
      try {
        await mockResponseInterceptor(error)
      } catch (e) {
        // Expected to throw after logout
      }
      
      // Verify axios.post was called for refresh
      expect(mockedAxios.post).toHaveBeenCalledWith('/api/v1/auth/refresh', {
        refresh_token: 'old-refresh',
      })
      
      // Verify logout was called (window.location.href changed)
      expect(window.location.href).toBe('/login')
    })

    it('should not retry if already retried', async () => {
      const originalRequest = { headers: {}, _retry: true }
      const error = { response: { status: 401 }, config: originalRequest }
      
      // The interceptor should return Promise.reject for non-retry cases
      // We just verify the mock axios.post is not called
      await expect(mockResponseInterceptor(error)).rejects.toEqual(error)
      expect(mockedAxios.post).not.toHaveBeenCalled()
    })

    it('should not refresh for non-401 errors', async () => {
      const originalRequest = { headers: {}, _retry: false }
      const error = { response: { status: 500 }, config: originalRequest }
      
      await expect(mockResponseInterceptor(error)).rejects.toEqual(error)
      expect(mockedAxios.post).not.toHaveBeenCalled()
    })
  })

  describe('Auth API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should register user', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { user: { id: '1' } } })
      
      const result = await apiModule.authApi.register({
        email: 'test@example.com',
        password: 'password123',
        name: 'Test User',
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/auth/register', {
        email: 'test@example.com',
        password: 'password123',
        name: 'Test User',
      })
      expect(result.data).toEqual({ user: { id: '1' } })
    })

    it('should login user with form data', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { access_token: 'token' } })
      
      const result = await apiModule.authApi.login({
        email: 'test@example.com',
        password: 'password123',
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith(
        '/auth/login',
        expect.any(URLSearchParams),
        expect.objectContaining({
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        })
      )
      expect(result.data).toEqual({ access_token: 'token' })
    })

    it('should refresh token', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { access_token: 'new-token' } })
      
      const result = await apiModule.authApi.refresh('refresh-token')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/auth/refresh', { refresh_token: 'refresh-token' })
      expect(result.data).toEqual({ access_token: 'new-token' })
    })

    it('should get current user', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { id: '1', email: 'test@example.com' } })
      
      const result = await apiModule.authApi.me()
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/auth/me')
      expect(result.data).toEqual({ id: '1', email: 'test@example.com' })
    })

    it('should update profile', async () => {
      mockAxiosInstance.patch.mockResolvedValue({ data: { id: '1', full_name: 'Updated' } })
      
      const result = await apiModule.authApi.updateProfile({ full_name: 'Updated' })
      
      expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/auth/me', { full_name: 'Updated' })
      expect(result.data).toEqual({ id: '1', full_name: 'Updated' })
    })

    it('should change password', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.authApi.changePassword({
        current_password: 'old',
        new_password: 'new',
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/auth/change-password', {
        current_password: 'old',
        new_password: 'new',
      })
      expect(result.data).toEqual({ success: true })
    })

    it('should get OAuth authorize URL', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { url: 'https://oauth.com' } })
      
      const result = await apiModule.authApi.oauthAuthorize('twitter', 'team1')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/auth/oauth/twitter/authorize', {
        params: { team_id: 'team1' },
      })
      expect(result.data).toEqual({ url: 'https://oauth.com' })
    })

    it('should handle OAuth callback', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { access_token: 'token' } })
      
      const result = await apiModule.authApi.oauthCallback('twitter', 'code', 'state')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/auth/oauth/twitter/callback', {
        params: { code: 'code', state: 'state' },
      })
      expect(result.data).toEqual({ access_token: 'token' })
    })
  })

  describe('Content API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should list posts with params', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.contentApi.listPosts({ status: 'published', page: 1 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/content/posts', {
        params: { status: 'published', page: 1 },
      })
      expect(result.data).toEqual([{ id: '1' }])
    })

    it('should get single post', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { id: '1', content_text: 'Test' } })
      
      const result = await apiModule.contentApi.getPost('1')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/content/posts/1')
      expect(result.data).toEqual({ id: '1', content_text: 'Test' })
    })

    it('should create post', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.contentApi.createPost({
        content_text: 'Test post',
        hashtags: ['#test'],
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/content/posts', {
        content_text: 'Test post',
        hashtags: ['#test'],
      })
      expect(result.data).toEqual({ id: '1' })
    })

    it('should update post', async () => {
      mockAxiosInstance.patch.mockResolvedValue({ data: { id: '1', content_text: 'Updated' } })
      
      const result = await apiModule.contentApi.updatePost('1', { content_text: 'Updated' })
      
      expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/content/posts/1', { content_text: 'Updated' })
      expect(result.data).toEqual({ id: '1', content_text: 'Updated' })
    })

    it('should delete post', async () => {
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.contentApi.deletePost('1')
      
      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/content/posts/1')
      expect(result.data).toEqual({ success: true })
    })

    it('should duplicate post', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '2' } })
      
      const result = await apiModule.contentApi.duplicatePost('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/content/posts/1/duplicate')
      expect(result.data).toEqual({ id: '2' })
    })

    it('should publish post now', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1', status: 'published' } })
      
      const result = await apiModule.contentApi.publishNow('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/content/posts/1/publish')
      expect(result.data).toEqual({ id: '1', status: 'published' })
    })

    it('should schedule post', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1', scheduled_at: '2024-01-01' } })
      
      const result = await apiModule.contentApi.schedulePost('1', '2024-01-01')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/content/posts/1/schedule', { scheduled_at: '2024-01-01' })
      expect(result.data).toEqual({ id: '1', scheduled_at: '2024-01-01' })
    })

    it('should get media list', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.contentApi.getMedia({ type: 'image' })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/content/media', { params: { type: 'image' } })
      expect(result.data).toEqual([{ id: '1' }])
    })

    it('should upload media', async () => {
      const file = new File(['content'], 'test.jpg', { type: 'image/jpeg' })
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.contentApi.uploadMedia(file, 'alt text', 'tags')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith(
        '/content/media/upload',
        expect.any(FormData),
        expect.objectContaining({
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      )
      expect(result.data).toEqual({ id: '1' })
    })

    it('should delete media', async () => {
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.contentApi.deleteMedia('1')
      
      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/content/media/1')
      expect(result.data).toEqual({ success: true })
    })
  })

  describe('Media API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should list media', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.mediaApi.list({ page: 1 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/media', { params: { page: 1 } })
      expect(result.data).toEqual([{ id: '1' }])
    })

    it('should upload media', async () => {
      const file = new File(['content'], 'test.jpg', { type: 'image/jpeg' })
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.mediaApi.upload(file)
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith(
        '/media/upload',
        expect.any(FormData),
        expect.objectContaining({
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      )
      expect(result.data).toEqual({ id: '1' })
    })

    it('should delete media', async () => {
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.mediaApi.delete('1')
      
      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/media/1')
      expect(result.data).toEqual({ success: true })
    })

    it('should generate image', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { url: 'https://image.com/img.jpg' } })
      
      const result = await apiModule.mediaApi.generateImage('A beautiful sunset', {
        width: 1024,
        height: 768,
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/media/generate', {
        prompt: 'A beautiful sunset',
        width: 1024,
        height: 768,
      })
      expect(result.data).toEqual({ url: 'https://image.com/img.jpg' })
    })
  })

  describe('Workflow API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should list templates', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.workflowApi.listTemplates('ai')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/workflows/templates', { params: { category: 'ai' } })
      expect(result.data).toEqual([{ id: '1' }])
    })

    it('should get template', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { id: '1', name: 'Template' } })
      
      const result = await apiModule.workflowApi.getTemplate('1')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/workflows/templates/1')
      expect(result.data).toEqual({ id: '1', name: 'Template' })
    })

    it('should create template', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.workflowApi.createTemplate({
        name: 'Test',
        prompt_template: 'Template',
        n8n_workflow_json: {},
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/workflows/templates', {
        name: 'Test',
        prompt_template: 'Template',
        n8n_workflow_json: {},
      })
      expect(result.data).toEqual({ id: '1' })
    })

    it('should update template', async () => {
      mockAxiosInstance.patch.mockResolvedValue({ data: { id: '1', name: 'Updated' } })
      
      const result = await apiModule.workflowApi.updateTemplate('1', { name: 'Updated' })
      
      expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/workflows/templates/1', { name: 'Updated' })
      expect(result.data).toEqual({ id: '1', name: 'Updated' })
    })

    it('should delete template', async () => {
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.workflowApi.deleteTemplate('1')
      
      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/workflows/templates/1')
      expect(result.data).toEqual({ success: true })
    })

    it('should generate workflow', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.workflowApi.generateWorkflow({ prompt: 'Create workflow', template_id: '1' })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/workflows/generate', { prompt: 'Create workflow', template_id: '1' })
      expect(result.data).toEqual({ id: '1' })
    })

    it('should list workflows', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.workflowApi.listWorkflows({ status: 'deployed' })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/workflows', { params: { status: 'deployed' } })
      expect(result.data).toEqual([{ id: '1' }])
    })

    it('should get workflow', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.workflowApi.getWorkflow('1')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/workflows/1')
      expect(result.data).toEqual({ id: '1' })
    })

    it('should deploy workflow', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.workflowApi.deployWorkflow('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/workflows/1/deploy')
      expect(result.data).toEqual({ success: true })
    })

    it('should undeploy workflow', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.workflowApi.undeployWorkflow('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/workflows/1/undeploy')
      expect(result.data).toEqual({ success: true })
    })

    it('should delete workflow', async () => {
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.workflowApi.deleteWorkflow('1')
      
      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/workflows/1')
      expect(result.data).toEqual({ success: true })
    })
  })

  describe('Accounts API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should list accounts', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.accountsApi.list()
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/accounts')
      expect(result.data).toEqual([{ id: '1' }])
    })

    it('should get account', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { id: '1', platform: 'twitter' } })
      
      const result = await apiModule.accountsApi.get('1')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/accounts/1')
      expect(result.data).toEqual({ id: '1', platform: 'twitter' })
    })

    it('should connect account', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.accountsApi.connect('twitter', 'team1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/accounts/connect', { platform: 'twitter', team_id: 'team1' })
      expect(result.data).toEqual({ id: '1' })
    })

    it('should disconnect account', async () => {
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.accountsApi.disconnect('1')
      
      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/accounts/1')
      expect(result.data).toEqual({ success: true })
    })

    it('should refresh account', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.accountsApi.refresh('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/accounts/1/refresh')
      expect(result.data).toEqual({ success: true })
    })

    it('should test account', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.accountsApi.test('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/accounts/1/test')
      expect(result.data).toEqual({ success: true })
    })
  })

  describe('Publishing API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should list publish queue', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.publishingApi.listQueue({ status: 'pending' })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/publishing/queue', { params: { status: 'pending' } })
      expect(result.data).toEqual([{ id: '1' }])
    })

    it('should get queue item', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { id: '1' } })
      
      const result = await apiModule.publishingApi.getQueueItem('1')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/publishing/queue/1')
      expect(result.data).toEqual({ id: '1' })
    })

    it('should retry queue item', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.publishingApi.retryQueueItem('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/publishing/queue/1/retry')
      expect(result.data).toEqual({ success: true })
    })

    it('should cancel queue item', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })
      
      const result = await apiModule.publishingApi.cancelQueueItem('1')
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/publishing/queue/1/cancel')
      expect(result.data).toEqual({ success: true })
    })

    it('should get history', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1' }] })
      
      const result = await apiModule.publishingApi.getHistory({ page: 1 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/publishing/history', { params: { page: 1 } })
      expect(result.data).toEqual([{ id: '1' }])
    })
  })

  describe('Analytics API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should get overview metrics', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { total_posts: 100 } })
      
      const result = await apiModule.analyticsApi.getOverview({ days: 30 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/analytics/overview', { params: { days: 30 } })
      expect(result.data).toEqual({ total_posts: 100 })
    })

    it('should get platform metrics', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ platform: 'twitter', followers: 1000 }] })
      
      const result = await apiModule.analyticsApi.getPlatformMetrics({ days: 7 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/analytics/platforms', { params: { days: 7 } })
      expect(result.data).toEqual([{ platform: 'twitter', followers: 1000 }])
    })

    it('should get post analytics', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: { engagement: 50 } })
      
      const result = await apiModule.analyticsApi.getPostAnalytics('1')
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/analytics/posts/1')
      expect(result.data).toEqual({ engagement: 50 })
    })

    it('should get engagement trends', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ date: '2024-01-01', engagement: 10 }] })
      
      const result = await apiModule.analyticsApi.getEngagementTrends({ days: 30 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/analytics/engagement', { params: { days: 30 } })
      expect(result.data).toEqual([{ date: '2024-01-01', engagement: 10 }])
    })

    it('should get follower growth', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ date: '2024-01-01', followers: 100 }] })
      
      const result = await apiModule.analyticsApi.getFollowerGrowth({ days: 30 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/analytics/followers', { params: { days: 30 } })
      expect(result.data).toEqual([{ date: '2024-01-01', followers: 100 }])
    })

    it('should get top posts', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [{ id: '1', engagement_count: 100 }] })
      
      const result = await apiModule.analyticsApi.getTopPosts({ limit: 10 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/analytics/top-posts', { params: { limit: 10 } })
      expect(result.data).toEqual([{ id: '1', engagement_count: 100 }])
    })

    it('should export report', async () => {
      const blob = new Blob(['csv,data'])
      mockAxiosInstance.get.mockResolvedValue({ data: blob })
      
      const result = await apiModule.analyticsApi.exportReport({ format: 'csv', days: 30 })
      
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/analytics/export', {
        params: { format: 'csv', days: 30 },
        responseType: 'blob',
      })
      expect(result.data).toBe(blob)
    })
  })

  describe('AI API', () => {
    beforeEach(() => {
      apiModule.setTokens('access-token', 'refresh-token')
    })

    it('should generate content', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { content: 'Generated', hashtags: ['#test'] } })
      
      const result = await apiModule.aiApi.generateContent({
        prompt: 'Create a post',
        platform: 'twitter',
        tone: 'professional',
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/generate-content', {
        prompt: 'Create a post',
        platform: 'twitter',
        tone: 'professional',
      })
      expect(result.data).toEqual({ content: 'Generated', hashtags: ['#test'] })
    })

    it('should improve content', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { content: 'Improved' } })
      
      const result = await apiModule.aiApi.improveContent({
        content: 'Original',
        platform: 'twitter',
        instruction: 'Make it better',
      })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/improve-content', {
        content: 'Original',
        platform: 'twitter',
        instruction: 'Make it better',
      })
      expect(result.data).toEqual({ content: 'Improved' })
    })

    it('should generate hashtags', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { hashtags: ['#ai', '#tech'] } })
      
      const result = await apiModule.aiApi.generateHashtags({ content: 'AI is great', platform: 'twitter', count: 5 })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/generate-hashtags', {
        content: 'AI is great',
        platform: 'twitter',
        count: 5,
      })
      expect(result.data).toEqual({ hashtags: ['#ai', '#tech'] })
    })

    it('should generate image prompt', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { prompt: 'A beautiful sunset' } })
      
      const result = await apiModule.aiApi.generateImagePrompt({ description: 'Sunset', style: 'photorealistic' })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/generate-image-prompt', {
        description: 'Sunset',
        style: 'photorealistic',
      })
      expect(result.data).toEqual({ prompt: 'A beautiful sunset' })
    })

    it('should analyze content', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { sentiment: 'positive', readability: 80 } })
      
      const result = await apiModule.aiApi.analyzeContent({ content: 'Great content!', platform: 'twitter' })
      
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/analyze-content', {
        content: 'Great content!',
        platform: 'twitter',
      })
      expect(result.data).toEqual({ sentiment: 'positive', readability: 80 })
    })
  })
})