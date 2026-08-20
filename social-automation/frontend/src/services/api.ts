import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import type { TokenResponse, ApiError } from '@/types'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api/v1'

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
})

let accessToken: string | null = localStorage.getItem('access_token')
let refreshToken: string | null = localStorage.getItem('refresh_token')
let isRefreshing = false
let failedQueue: Array<{
  resolve: (token: string) => void
  reject: (error: Error) => void
}> = []

const processQueue = (error: Error | null, token: string | null = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error)
    } else {
      prom.resolve(token!)
    }
  })
  failedQueue = []
}

api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiError>) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject })
        })
          .then((token) => {
            originalRequest.headers.Authorization = `Bearer ${token}`
            return api(originalRequest)
          })
          .catch((err) => Promise.reject(err))
      }

      originalRequest._retry = true
      isRefreshing = true

      try {
        const response = await axios.post<TokenResponse>(`${API_BASE}/auth/refresh`, {
          refresh_token: refreshToken,
        })

        accessToken = response.data.access_token
        refreshToken = response.data.refresh_token
        localStorage.setItem('access_token', accessToken)
        localStorage.setItem('refresh_token', refreshToken)

        processQueue(null, accessToken)

        originalRequest.headers.Authorization = `Bearer ${accessToken}`
        return api(originalRequest)
      } catch (refreshError) {
        processQueue(refreshError as Error, null)
        logout()
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    return Promise.reject(error)
  }
)

export const setTokens = (access: string, refresh: string) => {
  accessToken = access
  refreshToken = refresh
  localStorage.setItem('access_token', access)
  localStorage.setItem('refresh_token', refresh)
}

export const clearTokens = () => {
  accessToken = null
  refreshToken = null
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
}

export const logout = () => {
  clearTokens()
  window.location.href = '/login'
}

export const getAccessToken = () => accessToken
export const getRefreshToken = () => refreshToken

// Auth endpoints
export const authApi = {
  register: (data: { email: string; password: string; name: string }) =>
    api.post('/auth/register', data),
  login: (data: { email: string; password: string }) =>
    api.post('/auth/login', new URLSearchParams(data), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    }),
  refresh: (refresh_token: string) =>
    api.post('/auth/refresh', { refresh_token }),
  me: () => api.get('/auth/me'),
  updateProfile: (data: { full_name?: string; email?: string; avatar_url?: string }) =>
    api.patch('/auth/me', data),
  changePassword: (data: { current_password: string; new_password: string }) =>
    api.post('/auth/change-password', data),
  oauthAuthorize: (platform: string, teamId: string) =>
    api.get(`/auth/oauth/${platform}/authorize`, { params: { team_id: teamId } }),
  oauthCallback: (platform: string, code: string, state: string) =>
    api.get(`/auth/oauth/${platform}/callback`, { params: { code, state } }),
}

// Content endpoints
export const contentApi = {
  listPosts: (params?: { status?: string; page?: number; page_size?: number }) =>
    api.get('/content/posts', { params }),
  getPost: (id: string) => api.get(`/content/posts/${id}`),
  createPost: (data: {
    content_text?: string
    media_ids?: string[]
    platform_specific?: Record<string, unknown>
    hashtags?: string[]
    mention_accounts?: string[]
    link_url?: string
    link_preview_override?: Record<string, unknown>
    scheduled_at?: string
    targets?: Array<{ social_account_id: string }>
  }) => api.post('/content/posts', data),
  updatePost: (id: string, data: Partial<{
    content_text: string
    media_ids: string[]
    platform_specific: Record<string, unknown>
    hashtags: string[]
    mention_accounts: string[]
    link_url: string
    link_preview_override: Record<string, unknown>
    scheduled_at: string
    status: string
  }>) => api.patch(`/content/posts/${id}`, data),
  deletePost: (id: string) => api.delete(`/content/posts/${id}`),
  duplicatePost: (id: string) => api.post(`/content/posts/${id}/duplicate`),
  publishNow: (id: string) => api.post(`/content/posts/${id}/publish`),
  schedulePost: (id: string, scheduled_at: string) =>
    api.post(`/content/posts/${id}/schedule`, { scheduled_at }),
  getMedia: (params?: { page?: number; page_size?: number; type?: string }) =>
    api.get('/content/media', { params }),
  uploadMedia: (file: File, alt_text?: string, tags?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (alt_text) formData.append('alt_text', alt_text)
    if (tags) formData.append('tags', tags)
    return api.post('/content/media/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  deleteMedia: (id: string) => api.delete(`/content/media/${id}`),
}

// Media endpoints
export const mediaApi = {
  list: (params?: { page?: number; page_size?: number; type?: string }) =>
    api.get('/media', { params }),
  upload: (file: File, alt_text?: string, tags?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (alt_text) formData.append('alt_text', alt_text)
    if (tags) formData.append('tags', tags)
    return api.post('/media/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  delete: (id: string) => api.delete(`/media/${id}`),
  generateImage: (prompt: string, options?: {
    width?: number
    height?: number
    model?: string
    negative_prompt?: string
    steps?: number
    cfg_scale?: number
  }) => api.post('/media/generate', { prompt, ...options }),
}

// Workflow endpoints
export const workflowApi = {
  listTemplates: (category?: string) =>
    api.get('/workflows/templates', { params: { category } }),
  getTemplate: (id: string) => api.get(`/workflows/templates/${id}`),
  createTemplate: (data: {
    name: string
    description?: string
    prompt_template: string
    n8n_workflow_json: Record<string, unknown>
    category?: string
    tags?: string[]
    is_public?: boolean
  }) => api.post('/workflows/templates', data),
  updateTemplate: (id: string, data: Partial<{
    name: string
    description: string
    prompt_template: string
    n8n_workflow_json: Record<string, unknown>
    category: string
    tags: string[]
    is_public: boolean
  }>) => api.patch(`/workflows/templates/${id}`, data),
  deleteTemplate: (id: string) => api.delete(`/workflows/templates/${id}`),
  generateWorkflow: (data: { prompt: string; template_id?: string }) =>
    api.post('/workflows/generate', data),
  listWorkflows: (params?: { status?: string }) =>
    api.get('/workflows', { params }),
  getWorkflow: (id: string) => api.get(`/workflows/${id}`),
  deployWorkflow: (id: string) => api.post(`/workflows/${id}/deploy`),
  undeployWorkflow: (id: string) => api.post(`/workflows/${id}/undeploy`),
  deleteWorkflow: (id: string) => api.delete(`/workflows/${id}`),
}

// Accounts endpoints
export const accountsApi = {
  list: () => api.get('/accounts'),
  get: (id: string) => api.get(`/accounts/${id}`),
  connect: (platform: string, teamId: string) =>
    api.post('/accounts/connect', { platform, team_id: teamId }),
  disconnect: (id: string) => api.delete(`/accounts/${id}`),
  refresh: (id: string) => api.post(`/accounts/${id}/refresh`),
  test: (id: string) => api.post(`/accounts/${id}/test`),
}

// Publishing endpoints
export const publishingApi = {
  listQueue: (params?: { status?: string; page?: number; page_size?: number }) =>
    api.get('/publishing/queue', { params }),
  getQueueItem: (id: string) => api.get(`/publishing/queue/${id}`),
  retryQueueItem: (id: string) => api.post(`/publishing/queue/${id}/retry`),
  cancelQueueItem: (id: string) => api.post(`/publishing/queue/${id}/cancel`),
  getHistory: (params?: { page?: number; page_size?: number }) =>
    api.get('/publishing/history', { params }),
}

// Analytics endpoints
export const analyticsApi = {
  getOverview: (params?: { days?: number; platform?: string }) =>
    api.get('/analytics/overview', { params }),
  getPlatformMetrics: (params?: { days?: number }) =>
    api.get('/analytics/platforms', { params }),
  getPostAnalytics: (postId: string) =>
    api.get(`/analytics/posts/${postId}`),
  getEngagementTrends: (params?: { days?: number; platform?: string }) =>
    api.get('/analytics/engagement', { params }),
  getFollowerGrowth: (params?: { days?: number; platform?: string }) =>
    api.get('/analytics/followers', { params }),
  getTopPosts: (params?: { limit?: number; platform?: string }) =>
    api.get('/analytics/top-posts', { params }),
  exportReport: (params: { format: 'csv' | 'json'; days: number; platform?: string }) =>
    api.get('/analytics/export', { params, responseType: 'blob' }),
}

// AI endpoints
export const aiApi = {
  generateContent: (data: {
    prompt: string
    platform: string
    tone?: string
    length?: 'short' | 'medium' | 'long'
    include_hashtags?: boolean
    include_emojis?: boolean
    target_audience?: string
    brand_voice?: string
    template_id?: string
  }) => api.post('/ai/generate-content', data),
  improveContent: (data: {
    content: string
    platform: string
    instruction: string
  }) => api.post('/ai/improve-content', data),
  generateHashtags: (data: { content: string; platform: string; count?: number }) =>
    api.post('/ai/generate-hashtags', data),
  generateImagePrompt: (data: { description: string; style?: string }) =>
    api.post('/ai/generate-image-prompt', data),
  analyzeContent: (data: { content: string; platform: string }) =>
    api.post('/ai/analyze-content', data),
}

export default api