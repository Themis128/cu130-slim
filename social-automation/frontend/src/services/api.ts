import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import type { TokenResponse, ApiError } from '@/types'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api/v1'

/** Return a displayable URL for any media asset storage_path (abs or relative). */
export function mediaUrl(storagePath?: string | null): string {
  if (!storagePath) return ''
  return `${API_BASE}/media/view?path=${encodeURIComponent(storagePath)}`
}

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
})

let accessToken: string | null = null
let refreshToken: string | null = null

const getInitialAccessToken = () => {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('access_token')
  }
  return null
}

const getInitialRefreshToken = () => {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('refresh_token')
  }
  return null
}

accessToken = getInitialAccessToken()
refreshToken = getInitialRefreshToken()
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
    // Always read the freshest token — module-level var may be null if the module
    // initialised before login (e.g. SSR context or pre-auth import).
    const token = accessToken || (typeof window !== 'undefined' ? localStorage.getItem('access_token') : null)
    if (token) {
      accessToken = token  // keep module var in sync
      config.headers.Authorization = `Bearer ${token}`
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
      // Read the freshest refresh token — same staleness issue as access token
      const currentRefreshToken = refreshToken || (typeof window !== 'undefined' ? localStorage.getItem('refresh_token') : null)

      if (!currentRefreshToken) {
        logout()
        return Promise.reject(error)
      }

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
          refresh_token: currentRefreshToken,
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
  if (typeof window !== 'undefined') {
    localStorage.setItem('access_token', access)
    localStorage.setItem('refresh_token', refresh)
  }
}

export const clearTokens = () => {
  accessToken = null
  refreshToken = null
  if (typeof window !== 'undefined') {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }
}

export const logout = () => {
  clearTokens()
  if (typeof window !== 'undefined') {
    window.location.href = '/login'
  }
}

export const getAccessToken = () => {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('access_token')
  }
  return accessToken
}

export const getRefreshToken = () => {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('refresh_token')
  }
  return refreshToken
}

// Auth endpoints
export const authApi = {
  register: (data: { email: string; password: string; name: string }) =>
    api.post('/auth/register', data),
  login: (data: { email: string; password: string }) =>
    api.post('/auth/login', new URLSearchParams({ username: data.email, password: data.password }), {
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
  listPosts: (params?: { status?: string; platform?: string; page?: number; page_size?: number }) => {
    const p = { ...params }
    if (!p.status) delete p.status
    if (!p.platform) delete p.platform
    return api.get('/content/posts', { params: p })
  },
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
    target_account_ids?: string[]
    targets?: Array<{ social_account_id: string }>
    metadata?: Record<string, unknown>
  }) => {
    const { targets, target_account_ids, ...rest } = data
    const accountIds =
      target_account_ids ??
      targets?.map((t) => t.social_account_id) ??
      []
    return api.post('/content/posts', {
      ...rest,
      target_account_ids: accountIds,
    })
  },
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
  publishNow: (id: string) => api.post(`/content/posts/${id}/publish-now`),
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
  list: (params?: { page?: number; page_size?: number; type?: string }) => {
    const p = { ...params }
    if (!p.type) delete p.type
    return api.get('/media/assets', { params: p })
  },
  upload: (file: File, alt_text?: string, tags?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (alt_text) formData.append('alt_text', alt_text)
    if (tags) formData.append('tags', tags)
    return api.post('/media/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  delete: (id: string) => api.delete(`/media/assets/${id}`),
  generateImage: (prompt: string, options?: {
    width?: number
    height?: number
    model?: string
    provider?: string
    negative_prompt?: string
    steps?: number
    cfg_scale?: number
  }) =>
    api.post('/ai/generate-image', {
      prompt,
      provider: options?.provider ?? 'cloudflare',
      model: options?.model,
      negative_prompt: options?.negative_prompt ?? '',
      steps: options?.steps ?? 4,
      cfg_scale: options?.cfg_scale ?? 3.5,
      ...(options?.width != null ? { width: options.width } : {}),
      ...(options?.height != null ? { height: options.height } : {}),
    }),
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
  generateWorkflow: (data: { prompt: string; template_id?: string; model?: string; complexity?: string }) =>
    api.post('/workflows/generate', data),
  listWorkflows: (params?: { status?: string }) =>
    api.get('/workflows', { params }),
  getWorkflow: (id: string) => api.get(`/workflows/${id}`),
  deployWorkflow: (id: string) => api.post(`/workflows/deploy/${id}`),
  deleteWorkflow: (id: string) => api.delete(`/workflows/${id}`),
  undeployWorkflow: (id: string) => api.post(`/workflows/${id}/undeploy`),
}

// Accounts endpoints
export const accountsApi = {
  list: () => api.get('/accounts'),
  get: (id: string) => api.get(`/accounts/${id}`),
  connect: (platform: string, teamId: string) =>
    api.post('/accounts/connect', { platform, team_id: teamId }),
  disconnect: (id: string) => api.delete(`/accounts/${id}`),
  refresh: (id: string) => api.post(`/accounts/${id}/refresh`),
  validate: (id: string) => api.get(`/accounts/${id}/validate`),
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

// Analytics endpoints (self-hosted social-api → Postgres)
export const analyticsApi = {
  getOverview: (params?: { days?: number; platform?: string }) =>
    api.get('/analytics/overview', { params }),
  getPlatformMetrics: (params?: { days?: number }) =>
    api.get('/analytics/platforms', { params }),
  getPostAnalytics: (postId: string) =>
    api.get(`/analytics/posts/${postId}/metrics`),
  getEngagementTrends: (params?: { days?: number; platform?: string }) =>
    api.get('/analytics/engagement', { params }),
  getFollowerGrowth: (params?: { days?: number; platform?: string }) =>
    api.get('/analytics/followers', { params }),
  getTopPosts: (params?: { limit?: number; platform?: string; days?: number }) =>
    api.get('/analytics/top-posts', { params }),
  exportReport: (params: { format: 'csv' | 'json'; days: number; platform?: string }) =>
    api.get('/analytics/reports/export', { params }),
  syncFromPlatforms: (data?: { days?: number; async_mode?: boolean }) =>
    api.post('/analytics/sync', data || { days: 365, async_mode: true }),
  listSnapshots: (params?: { days?: number; post_id?: string; limit?: number }) =>
    api.get('/analytics/snapshots', { params }),
}

// AI endpoints
export const aiProvidersApi = {
  getCatalog: () => api.get('/ai-providers/catalog'),
  list: () => api.get('/ai-providers'),
  upsert: (name: string, data: {
    display_name?: string
    api_key?: string
    base_url?: string
    default_model?: string
    is_enabled?: boolean
    is_default?: boolean
  }) => api.put(`/ai-providers/${name}`, data),
  delete: (name: string) => api.delete(`/ai-providers/${name}`),
  test: (name: string) => api.post(`/ai-providers/${name}/test`),
  listModels: (name: string) => api.get(`/ai-providers/${name}/models`),
}

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
    goal?: string
    instruction?: string
  }) =>
    api.post('/ai/improve-content', {
      content: data.content,
      platform: data.platform,
      goal: data.goal || data.instruction || 'engagement',
    }),
  generateHashtags: (data: { content: string; platform: string; count?: number; max_hashtags?: number }) =>
    api.post('/ai/generate-hashtags', {
      content: data.content,
      platform: data.platform,
      max_hashtags: data.max_hashtags ?? data.count ?? 10,
    }),
  generateImagePrompt: (data: { description: string; style?: string }) =>
    api.post('/ai/generate-image-prompt', data),
  analyzeContent: (data: { content: string; platform: string }) =>
    api.post('/ai/analyze-content', data),
  generateCarousel: (data: {
    topic: string
    num_slides?: number
    platform?: string
    tone?: string
    include_cta?: boolean
    provider?: string
  }) => api.post('/ai/generate-carousel', data),
  generateCarouselPipeline: (data: {
    topic: string
    num_slides?: number
    platform?: string
    tone?: string
    include_cta?: boolean
    text_model?: string
    txt2img_model?: string
  }) => api.post('/ai/generate-carousel-pipeline', data),
  enhanceImagePrompt: (description: string, style?: string) =>
    api.post('/ai/generate-image-prompt', { description, style: style ?? 'photorealistic' }),
  autoConfigurePrompt: (prompt: string, context?: 'image' | 'carousel' | 'auto') =>
    api.post('/ai/auto-configure', { prompt, context: context ?? 'auto' }),
  saveGenerationTemplate: (data: {
    name: string
    category?: string
    prompt_template: string
    settings?: Record<string, unknown>
    tags?: string[]
    is_public?: boolean
  }) => api.post('/ai/save-generation-template', data),
  getWorkflowConfig: (contentType: string) =>
    api.get(`/ai/workflow-config/${contentType}`),
  seedDefaultWorkflows: () => api.post('/ai/seed-default-workflows'),
  transcribeAudio: (file: File, model?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (model) form.append('model', model)
    return api.post('/ai/transcribe', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  /** Queue a batch of requests against a Workers AI model (?queueRequest=true). */
  submitBatchInference: (data: {
    model: string
    requests: Array<Record<string, unknown> & { external_reference?: string }>
  }) => api.post('/ai/workers-ai/batch', data),
  /** Poll/retrieve results of a previously submitted batch request. */
  retrieveBatchInference: (data: { model: string; request_id: string }) =>
    api.post('/ai/workers-ai/batch/retrieve', data),
}

export default api