import { vi, describe, it, expect, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

const hoisted = vi.hoisted(() => {
  const group = () =>
    new Proxy({} as Record<string, ReturnType<typeof vi.fn>>, {
      get: (target, prop: string) => {
        if (!target[prop]) {
          target[prop] = vi.fn().mockResolvedValue({ data: {} })
        }
        return target[prop]
      },
    })
  return {
    contentApi: group(),
    mediaApi: group(),
    workflowApi: group(),
    accountsApi: group(),
    profileApi: group(),
    secretsApi: group(),
    publishingApi: group(),
    analyticsApi: group(),
    aiApi: group(),
    aiProvidersApi: group(),
    linkedinApi: group(),
    brandApi: group(),
    auditApi: group(),
    inboxApi: group(),
    toast: { success: vi.fn(), error: vi.fn() },
  }
})

vi.mock('@/services/api', () => ({
  contentApi: hoisted.contentApi,
  mediaApi: hoisted.mediaApi,
  workflowApi: hoisted.workflowApi,
  accountsApi: hoisted.accountsApi,
  profileApi: hoisted.profileApi,
  secretsApi: hoisted.secretsApi,
  publishingApi: hoisted.publishingApi,
  analyticsApi: hoisted.analyticsApi,
  aiApi: hoisted.aiApi,
  aiProvidersApi: hoisted.aiProvidersApi,
  linkedinApi: hoisted.linkedinApi,
  brandApi: hoisted.brandApi,
  auditApi: hoisted.auditApi,
  inboxApi: hoisted.inboxApi,
  getAccessToken: vi.fn(() => 'tok'),
}))

vi.mock('react-hot-toast', () => ({ default: hoisted.toast }))

import * as H from './useQueries'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const FILE = new File(['x'], 'f.png')

// [label, render hook, apiGroup.method, expected args]
const QUERY_CASES: Array<[string, () => unknown, string, string, unknown[]?]> = [
  ['usePosts', () => H.usePosts({ status: 'draft' }), 'contentApi', 'listPosts', [{ status: 'draft' }]],
  ['usePost', () => H.usePost('p1'), 'contentApi', 'getPost', ['p1']],
  ['usePillars', () => H.usePillars(), 'contentApi', 'listPillars'],
  ['useBriefs', () => H.useBriefs(), 'contentApi', 'listBriefs'],
  ['useMedia', () => H.useMedia({ type: 'image' }), 'mediaApi', 'list', [{ type: 'image' }]],
  ['useTemplates', () => H.useTemplates('cat'), 'workflowApi', 'listTemplates', ['cat']],
  ['useTemplate', () => H.useTemplate('t1'), 'workflowApi', 'getTemplate', ['t1']],
  ['useWorkflows', () => H.useWorkflows({ status: 'active' }), 'workflowApi', 'listWorkflows', [{ status: 'active' }]],
  ['useContentTemplates', () => H.useContentTemplates({ platform: 'x' }), 'workflowApi', 'listContentTemplates', [{ platform: 'x' }]],
  ['useAccounts', () => H.useAccounts(), 'accountsApi', 'list'],
  ['usePageProfile', () => H.usePageProfile('a1'), 'accountsApi', 'getPageProfile', ['a1']],
  ['useProfile', () => H.useProfile('a1'), 'profileApi', 'getProfile', ['a1']],
  ['usePublishQueue', () => H.usePublishQueue({ status: 'pending' }), 'publishingApi', 'listQueue', [{ status: 'pending' }]],
  ['useScheduledPosts', () => H.useScheduledPosts(), 'contentApi', 'listPosts', [{ status: 'scheduled', page_size: 100 }]],
  ['useOverviewMetrics', () => H.useOverviewMetrics(7), 'analyticsApi', 'getOverview', [{ days: 7 }]],
  ['usePlatformMetrics', () => H.usePlatformMetrics(7), 'analyticsApi', 'getPlatformMetrics', [{ days: 7 }]],
  ['usePostAnalytics', () => H.usePostAnalytics('p1'), 'analyticsApi', 'getPostAnalytics', ['p1']],
  ['useTopPosts', () => H.useTopPosts(5, 'x', 7), 'analyticsApi', 'getTopPosts', [{ limit: 5, platform: 'x', days: 7 }]],
  ['useEngagementTrends', () => H.useEngagementTrends(7, 'x'), 'analyticsApi', 'getEngagementTrends', [{ days: 7, platform: 'x' }]],
  ['useFollowerGrowth', () => H.useFollowerGrowth(7), 'analyticsApi', 'getFollowerGrowth', [{ days: 7 }]],
  ['useBotSummary', () => H.useBotSummary(7), 'analyticsApi', 'getBotSummary', [{ days: 7 }]],
  ['useCloudflareAIUsage', () => H.useCloudflareAIUsage(7), 'analyticsApi', 'getCloudflareAIUsage', [{ days: 7 }]],
  ['useCloudflareOverview', () => H.useCloudflareOverview(7), 'analyticsApi', 'getCloudflareOverview', [{ days: 7 }]],
  ['usePublishPipeline', () => H.usePublishPipeline(7), 'analyticsApi', 'getPublishPipeline', [{ days: 7 }]],
  ['useAdCampaigns', () => H.useAdCampaigns(7), 'analyticsApi', 'getAdCampaigns', [{ days: 7 }]],
  ['useInitiatives', () => H.useInitiatives(7), 'analyticsApi', 'getInitiatives', [{ days: 7 }]],
  ['useAIProviderCatalog', () => H.useAIProviderCatalog(), 'aiProvidersApi', 'getCatalog'],
  ['useAIProviders', () => H.useAIProviders(), 'aiProvidersApi', 'list'],
  ['useAIProviderModels', () => H.useAIProviderModels('openai'), 'aiProvidersApi', 'listModels', ['openai']],
  ['useAIProviderUsage', () => H.useAIProviderUsage(7), 'aiProvidersApi', 'getUsage', [{ days: 7 }]],
  ['useLinkedinValidateAccount', () => H.useLinkedinValidateAccount('a1'), 'linkedinApi', 'validateAccount', ['a1']],
  ['useLinkedinFollowers', () => H.useLinkedinFollowers('a1'), 'linkedinApi', 'followers', ['a1']],
  ['useLinkedinPostAnalytics', () => H.useLinkedinPostAnalytics('urn', 'a1'), 'linkedinApi', 'postAnalytics', ['urn', 'a1']],
  ['useLinkedinOrganizationAnalytics', () => H.useLinkedinOrganizationAnalytics('a1'), 'linkedinApi', 'organizationAnalytics', ['a1']],
  ['useBrand', () => H.useBrand(), 'brandApi', 'get'],
  ['useBrandAssets', () => H.useBrandAssets(), 'brandApi', 'listAssets'],
  ['useAuditLogs', () => H.useAuditLogs({ action: 'login' }), 'auditApi', 'list', [{ action: 'login' }]],
  ['useInbox', () => H.useInbox(), 'inboxApi', 'getInbox'],
]

// [label, hook, vars, apiGroup.method, expected args]
const MUTATION_CASES: Array<[string, () => { mutateAsync: (v: unknown) => Promise<unknown> }, unknown, string, string, unknown[]?]> = [
  ['useCreatePost', () => H.useCreatePost(), { content_text: 't' }, 'contentApi', 'createPost', [{ content_text: 't' }]],
  ['useUpdatePost', () => H.useUpdatePost(), { id: 'p1', data: { content_text: 'u' } }, 'contentApi', 'updatePost', ['p1', { content_text: 'u' }]],
  ['useDeletePost', () => H.useDeletePost(), 'p1', 'contentApi', 'deletePost'],
  ['usePublishPost', () => H.usePublishPost(), 'p1', 'contentApi', 'publishNow'],
  ['useSchedulePost', () => H.useSchedulePost(), { id: 'p1', scheduled_at: 's' }, 'contentApi', 'schedulePost', ['p1', 's']],
  ['useSubmitForReview', () => H.useSubmitForReview(), 'p1', 'contentApi', 'submitForReview'],
  ['useApprovePost', () => H.useApprovePost(), { id: 'p1', body: 'ok' }, 'contentApi', 'approvePost', ['p1', 'ok']],
  ['useRejectPost', () => H.useRejectPost(), { id: 'p1' }, 'contentApi', 'rejectPost', ['p1', undefined]],
  ['useAddComment', () => H.useAddComment(), { id: 'p1', body: 'c', action: 'a' }, 'contentApi', 'addComment', ['p1', 'c', 'a']],
  ['useCreatePillar', () => H.useCreatePillar(), { name: 'P' }, 'contentApi', 'createPillar', [{ name: 'P' }]],
  ['useUpdatePillar', () => H.useUpdatePillar(), { id: 'p1', data: { name: 'P' } }, 'contentApi', 'updatePillar', ['p1', { name: 'P' }]],
  ['useDeletePillar', () => H.useDeletePillar(), 'p1', 'contentApi', 'deletePillar'],
  ['useCreateBrief', () => H.useCreateBrief(), { title: 'T' }, 'contentApi', 'createBrief', [{ title: 'T' }]],
  ['useDeleteBrief', () => H.useDeleteBrief(), 'b1', 'contentApi', 'deleteBrief'],
  ['useUploadMedia', () => H.useUploadMedia(), { file: FILE, alt_text: 'a', tags: 't' }, 'mediaApi', 'upload', [FILE, 'a', 't']],
  ['useDeleteMedia', () => H.useDeleteMedia(), 'm1', 'mediaApi', 'delete'],
  ['useBulkDeleteMedia', () => H.useBulkDeleteMedia(), ['m1'], 'mediaApi', 'bulkDelete', [['m1']]],
  ['useGenerateImage', () => H.useGenerateImage(), { prompt: 'p', options: { width: 100 } }, 'mediaApi', 'generateImage', ['p', { width: 100 }]],
  ['useGenerateVideo', () => H.useGenerateVideo(), { prompt: 'p', options: { steps: 4 } }, 'mediaApi', 'generateVideo', ['p', { steps: 4 }]],
  ['useCreateTemplate', () => H.useCreateTemplate(), { name: 'T' }, 'workflowApi', 'createTemplate', [{ name: 'T' }]],
  ['useGenerateWorkflow', () => H.useGenerateWorkflow(), { prompt: 'p' }, 'workflowApi', 'generateWorkflow', [{ prompt: 'p' }]],
  ['useDeployWorkflow', () => H.useDeployWorkflow(), 'w1', 'workflowApi', 'deployWorkflow'],
  ['useConnectAccount', () => H.useConnectAccount(), { platform: 'x', teamId: 't1' }, 'accountsApi', 'connect', ['x', 't1']],
  ['useDisconnectAccount', () => H.useDisconnectAccount(), 'a1', 'accountsApi', 'disconnect'],
  ['useSyncBusinessAccounts', () => H.useSyncBusinessAccounts(), 'a1', 'accountsApi', 'syncBusinessAccounts', ['a1']],
  ['useSetBusinessAccount', () => H.useSetBusinessAccount(), { id: 'a1', businessAccountId: 'b1' }, 'accountsApi', 'setBusinessAccount', ['a1', 'b1']],
  ['useUpdatePageProfile', () => H.useUpdatePageProfile(), { id: 'a1', data: { about: 'x' } }, 'accountsApi', 'updatePageProfile', ['a1', { about: 'x' }]],
  ['useUploadProfilePicture', () => H.useUploadProfilePicture(), { id: 'a1', file: FILE }, 'accountsApi', 'uploadProfilePicture', ['a1', FILE]],
  ['useUploadCoverPhoto', () => H.useUploadCoverPhoto(), { id: 'a1', file: FILE }, 'accountsApi', 'uploadCoverPhoto', ['a1', FILE]],
  ['useSetSecret', () => H.useSetSecret(), { key: 'K', value: 'v', description: 'd' }, 'secretsApi', 'set', ['K', 'v', 'd']],
  ['useUpdateProfile', () => H.useUpdateProfile(), { id: 'a1', data: { about: 'x' } }, 'profileApi', 'updateProfile', ['a1', { about: 'x' }]],
  ['useUploadProfileProfilePicture', () => H.useUploadProfileProfilePicture(), { id: 'a1', file: FILE }, 'profileApi', 'uploadProfilePicture', ['a1', FILE]],
  ['useUploadProfileCoverPhoto', () => H.useUploadProfileCoverPhoto(), { id: 'a1', file: FILE }, 'profileApi', 'uploadCoverPhoto', ['a1', FILE]],
  ['useProfileLogin', () => H.useProfileLogin(), { id: 'a1', data: { username: 'u' } }, 'profileApi', 'login', ['a1', { username: 'u' }]],
  ['useAssignManageTask', () => H.useAssignManageTask(), { id: 'a1', businessId: 'b1', businessUserId: 'u1' }, 'accountsApi', 'assignManageTask', ['a1', 'b1', 'u1']],
  ['useRetryQueueItem', () => H.useRetryQueueItem(), 'q1', 'publishingApi', 'retryQueueItem'],
  ['useGenerateContent', () => H.useGenerateContent(), { prompt: 'p' }, 'aiApi', 'generateContent', [{ prompt: 'p' }]],
  ['useCreateContentTemplate', () => H.useCreateContentTemplate(), { name: 'T' }, 'workflowApi', 'createContentTemplate', [{ name: 'T' }]],
  ['useDeleteContentTemplate', () => H.useDeleteContentTemplate(), 't1', 'workflowApi', 'deleteContentTemplate'],
  ['useImproveContent', () => H.useImproveContent(), { content: 'c' }, 'aiApi', 'improveContent', [{ content: 'c' }]],
  ['useGenerateHashtags', () => H.useGenerateHashtags(), { content: 'c' }, 'aiApi', 'generateHashtags', [{ content: 'c' }]],
  ['useAnalyzeContent', () => H.useAnalyzeContent(), { content: 'c' }, 'aiApi', 'analyzeContent', [{ content: 'c' }]],
  ['useAnalyzeSeo', () => H.useAnalyzeSeo(), { content: 'c' }, 'aiApi', 'analyzeSeo', [{ content: 'c' }]],
  ['useUpsertAIProvider', () => H.useUpsertAIProvider(), { name: 'p', data: {} }, 'aiProvidersApi', 'upsert', ['p', {}]],
  ['useDeleteAIProvider', () => H.useDeleteAIProvider(), 'p', 'aiProvidersApi', 'delete', ['p']],
  ['useTestAIProvider', () => H.useTestAIProvider(), 'p', 'aiProvidersApi', 'test', ['p']],
  ['useResetCircuitBreaker', () => H.useResetCircuitBreaker(), 'p', 'aiProvidersApi', 'resetCircuit', ['p']],
  ['useLinkedinGeneratePost', () => H.useLinkedinGeneratePost(), { topic: 't' }, 'linkedinApi', 'generatePost', [{ topic: 't' }]],
  ['useLinkedinGenerateArticle', () => H.useLinkedinGenerateArticle(), { topic: 't' }, 'linkedinApi', 'generateArticle', [{ topic: 't' }]],
  ['useLinkedinGenerateHashtags', () => H.useLinkedinGenerateHashtags(), { content: 'c' }, 'linkedinApi', 'generateHashtags', [{ content: 'c' }]],
  ['useLinkedinImprovePost', () => H.useLinkedinImprovePost(), { content: 'c' }, 'linkedinApi', 'improvePost', [{ content: 'c' }]],
  ['useLinkedinGenerateComment', () => H.useLinkedinGenerateComment(), { post_text: 'p' }, 'linkedinApi', 'generateComment', [{ post_text: 'p' }]],
  ['useLinkedinBestTime', () => H.useLinkedinBestTime(), { account_type: 'org' }, 'linkedinApi', 'bestTime', ['org']],
  ['useLinkedinPublish', () => H.useLinkedinPublish(), { account_id: 'a', commentary: 'c' }, 'linkedinApi', 'publish', [{ account_id: 'a', commentary: 'c' }]],
  ['useCreateBrand', () => H.useCreateBrand(), { name: 'B' }, 'brandApi', 'create', [{ name: 'B' }]],
  ['useUpdateBrand', () => H.useUpdateBrand(), { name: 'B' }, 'brandApi', 'update', [{ name: 'B' }]],
  ['useUpdateBrandVoice', () => H.useUpdateBrandVoice(), { tone_dimensions: {} }, 'brandApi', 'updateVoice', [{ tone_dimensions: {} }]],
  ['useUpdateBrandVisual', () => H.useUpdateBrandVisual(), { primary_color: '#fff' }, 'brandApi', 'updateVisual', [{ primary_color: '#fff' }]],
  ['useCompileGuidelines', () => H.useCompileGuidelines(), undefined, 'brandApi', 'compileGuidelines'],
  ['useCreateBrandAsset', () => H.useCreateBrandAsset(), { name: 'A' }, 'brandApi', 'createAsset', [{ name: 'A' }]],
  ['useDeleteBrandAsset', () => H.useDeleteBrandAsset(), 'a1', 'brandApi', 'deleteAsset'],
]

describe('useQueries hook surface', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it.each(QUERY_CASES)('%s fetches via %s.%s', async (_l, useHook, group, method, args) => {
    renderHook(useHook, { wrapper })
    await waitFor(() => {
      expect(hoisted[group as keyof typeof hoisted][method]).toHaveBeenCalled()
    })
    if (args) {
      expect(hoisted[group as keyof typeof hoisted][method]).toHaveBeenCalledWith(...args)
    }
  })

  it.each(MUTATION_CASES)('%s mutates via %s.%s', async (_l, useHook, vars, group, method, args) => {
    const { result } = renderHook(useHook, { wrapper })
    await act(async () => {
      await result.current.mutateAsync(vars)
    })
    const mock = hoisted[group as keyof typeof hoisted][method]
    expect(mock).toHaveBeenCalled()
    const call = mock.mock.calls[0]
    if (args) {
      args.forEach((arg, i) => {
        expect(call[i]).toEqual(arg)
      })
    } else {
      expect(call[0]).toEqual(vars)
    }
  })
})

describe('useQueries select transforms', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('useScheduledPosts extracts posts from { posts: [...] } shape', async () => {
    hoisted.contentApi.listPosts.mockResolvedValueOnce({ data: { posts: [{ id: '1' }] } })
    const { result } = renderHook(() => H.useScheduledPosts(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([{ id: '1' }])
  })

  it('useScheduledPosts falls back to items/array shapes', async () => {
    hoisted.contentApi.listPosts.mockResolvedValueOnce({ data: { items: [{ id: '2' }] } })
    const { result } = renderHook(() => H.useScheduledPosts(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([{ id: '2' }])

    hoisted.contentApi.listPosts.mockResolvedValueOnce({ data: [{ id: '3' }] })
    const { result: r2 } = renderHook(() => H.useScheduledPosts(), { wrapper })
    await waitFor(() => expect(r2.current.isSuccess).toBe(true))
    expect(r2.current.data).toEqual([{ id: '3' }])
  })

  it('useEngagementTrends maps total to value', async () => {
    hoisted.analyticsApi.getEngagementTrends.mockResolvedValueOnce({
      data: [{ date: 'd', likes: 1, comments: 0, shares: 0, clicks: 0, total: 5 }],
    })
    const { result } = renderHook(() => H.useEngagementTrends(7), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.[0].value).toBe(5)
  })

  it('useSyncBusinessAccounts toasts the synced count', async () => {
    hoisted.accountsApi.syncBusinessAccounts.mockResolvedValueOnce({ data: { synced: 2 } })
    const { result } = renderHook(() => H.useSyncBusinessAccounts(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync('a1')
    })
    expect(hoisted.toast.success).toHaveBeenCalledWith('Synced 2 business accounts')
  })

  it('useTestAIProvider surfaces ok/error responses', async () => {
    hoisted.aiProvidersApi.test.mockResolvedValueOnce({ data: { ok: true, response: 'pong' } })
    const { result } = renderHook(() => H.useTestAIProvider(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync('p')
    })
    expect(hoisted.toast.success).toHaveBeenCalledWith('Connected! "pong"')

    hoisted.aiProvidersApi.test.mockResolvedValueOnce({ data: { ok: false, error: 'nope' } })
    await act(async () => {
      await result.current.mutateAsync('p')
    })
    expect(hoisted.toast.error).toHaveBeenCalledWith('Test failed: nope')
  })

  it('onError toasts fall back to default messages', async () => {
    hoisted.contentApi.createPost.mockRejectedValueOnce(new Error('boom'))
    const { result } = renderHook(() => H.useCreatePost(), { wrapper })
    await act(async () => {
      await expect(result.current.mutateAsync({ content_text: 'x' })).rejects.toThrow()
    })
    expect(hoisted.toast.error).toHaveBeenCalledWith('Failed to create post')
  })

  it('onError uses server detail when present', async () => {
    hoisted.contentApi.createPost.mockRejectedValueOnce({
      response: { data: { detail: 'Duplicate slug' } },
    })
    const { result } = renderHook(() => H.useCreatePost(), { wrapper })
    await act(async () => {
      await expect(result.current.mutateAsync({ content_text: 'x' })).rejects.toEqual({ response: { data: { detail: 'Duplicate slug' } } })
    })
    expect(hoisted.toast.error).toHaveBeenCalledWith('Duplicate slug')
  })

  it('usePost is disabled for empty id', () => {
    const { result } = renderHook(() => H.usePost(''), { wrapper })
    expect(result.current.fetchStatus).toBe('idle')
    expect(hoisted.contentApi.getPost).not.toHaveBeenCalled()
  })
})

// [hook, vars, apiGroup, method, fallback toast when the server sends no detail]
const ERROR_FALLBACK_CASES: Array<[string, () => { mutateAsync: (v: unknown) => Promise<unknown> }, unknown, string, string, string]> = [
  ['useUpdatePageProfile', () => H.useUpdatePageProfile(), { id: 'a', data: {} }, 'accountsApi', 'updatePageProfile', 'Failed to update page profile'],
  ['useUploadProfilePicture', () => H.useUploadProfilePicture(), { id: 'a', file: FILE }, 'accountsApi', 'uploadProfilePicture', 'Failed to upload profile picture'],
  ['useUploadCoverPhoto', () => H.useUploadCoverPhoto(), { id: 'a', file: FILE }, 'accountsApi', 'uploadCoverPhoto', 'Failed to upload cover photo'],
  ['useSetSecret', () => H.useSetSecret(), { key: 'k', value: 'v' }, 'secretsApi', 'set', 'Failed to save credential'],
  ['useUpdateProfile', () => H.useUpdateProfile(), { id: 'a', data: {} }, 'profileApi', 'updateProfile', 'Failed to update profile'],
  ['useUploadProfileProfilePicture', () => H.useUploadProfileProfilePicture(), { id: 'a', file: FILE }, 'profileApi', 'uploadProfilePicture', 'Failed to upload profile picture'],
  ['useUploadProfileCoverPhoto', () => H.useUploadProfileCoverPhoto(), { id: 'a', file: FILE }, 'profileApi', 'uploadCoverPhoto', 'Failed to upload cover photo'],
  ['useProfileLogin', () => H.useProfileLogin(), { id: 'a', data: {} }, 'profileApi', 'login', 'Login failed'],
  ['useAssignManageTask', () => H.useAssignManageTask(), { id: 'a', businessId: 'b' }, 'accountsApi', 'assignManageTask', 'Failed to assign MANAGE task'],
  ['useLinkedinPublish', () => H.useLinkedinPublish(), { text: 't' }, 'linkedinApi', 'publish', 'Failed to publish to LinkedIn'],
  ['useCreateBrand', () => H.useCreateBrand(), { name: 'B' }, 'brandApi', 'create', 'Failed to create brand'],
  ['useUpdateBrand', () => H.useUpdateBrand(), { name: 'B' }, 'brandApi', 'update', 'Failed to update brand'],
  ['useUpdateBrandVoice', () => H.useUpdateBrandVoice(), {}, 'brandApi', 'updateVoice', 'Failed to update brand voice'],
  ['useUpdateBrandVisual', () => H.useUpdateBrandVisual(), {}, 'brandApi', 'updateVisual', 'Failed to update brand visual'],
  ['useCompileGuidelines', () => H.useCompileGuidelines(), undefined, 'brandApi', 'compileGuidelines', 'Failed to compile guidelines'],
  ['useCreateBrandAsset', () => H.useCreateBrandAsset(), { name: 'logo' }, 'brandApi', 'createAsset', 'Failed to add brand asset'],
]

describe('useQueries onError fallbacks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it.each(ERROR_FALLBACK_CASES)('%s toasts its default message when the server sends no detail', async (_l, hook, vars, group, method, fallback) => {
    const api = (hoisted as unknown as Record<string, Record<string, ReturnType<typeof vi.fn>>>)[group]
    api[method].mockRejectedValueOnce({ response: { status: 500, data: {} } })
    const { result } = renderHook(hook, { wrapper })
    await act(async () => {
      await expect(result.current.mutateAsync(vars)).rejects.toBeTruthy()
    })
    expect(hoisted.toast.error).toHaveBeenCalledWith(fallback)
  })
})

describe('useQueries success-toast and select edge shapes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('useUploadMedia stays quiet when silent is set', async () => {
    const { result } = renderHook(() => H.useUploadMedia(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync({ file: FILE, silent: true })
    })
    expect(hoisted.toast.success).not.toHaveBeenCalled()
  })

  it('useBulkDeleteMedia pluralises the toast', async () => {
    const { result } = renderHook(() => H.useBulkDeleteMedia(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync(['a'])
    })
    expect(hoisted.toast.success).toHaveBeenLastCalledWith('1 item deleted')
    await act(async () => {
      await result.current.mutateAsync(['a', 'b'])
    })
    expect(hoisted.toast.success).toHaveBeenLastCalledWith('2 items deleted')
  })

  it('useSyncBusinessAccounts uses the singular for one account and 0 when missing', async () => {
    hoisted.accountsApi.syncBusinessAccounts.mockResolvedValueOnce({ data: { synced: 1 } })
    const { result } = renderHook(() => H.useSyncBusinessAccounts(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync('a1')
    })
    expect(hoisted.toast.success).toHaveBeenLastCalledWith('Synced 1 business account')
    hoisted.accountsApi.syncBusinessAccounts.mockResolvedValueOnce({ data: null })
    await act(async () => {
      await result.current.mutateAsync('a1')
    })
    expect(hoisted.toast.success).toHaveBeenLastCalledWith('Synced 0 business accounts')
  })

  it('analytics selects tolerate an empty payload', async () => {
    hoisted.analyticsApi.getEngagementTrends.mockResolvedValueOnce({ data: null })
    hoisted.analyticsApi.getFollowerGrowth.mockResolvedValueOnce({ data: null })
    const { result: trends } = renderHook(() => H.useEngagementTrends(7), { wrapper })
    const { result: growth } = renderHook(() => H.useFollowerGrowth(7), { wrapper })
    await waitFor(() => expect(trends.current.isSuccess && growth.current.isSuccess).toBe(true))
    expect(trends.current.data).toEqual([])
    expect(growth.current.data).toEqual([])
  })

  it('useEngagementTrends defaults a missing total to 0', async () => {
    hoisted.analyticsApi.getEngagementTrends.mockResolvedValueOnce({ data: [{ date: 'd' }] })
    const { result } = renderHook(() => H.useEngagementTrends(7), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.[0].value).toBe(0)
  })
})

function fastRetryWrapper({ children }: { children: ReactNode }) {
  // Hook-level `retry` predicates still apply; only the backoff is zeroed.
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useQueries retry predicates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const RETRY_CASES: Array<[string, () => { isError: boolean }, string, string, number]> = [
    ['useAccounts', () => H.useAccounts(), 'accountsApi', 'list', 403],
    ['usePageProfile', () => H.usePageProfile('a1'), 'accountsApi', 'getPageProfile', 400],
    ['useProfile', () => H.useProfile('a1'), 'profileApi', 'getProfile', 401],
  ]

  it.each(RETRY_CASES)('%s does not retry auth/client errors', async (_l, hook, group, method, status) => {
    const api = (hoisted as unknown as Record<string, Record<string, ReturnType<typeof vi.fn>>>)[group]
    api[method].mockRejectedValue({ response: { status } })
    const { result } = renderHook(hook, { wrapper: fastRetryWrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(api[method]).toHaveBeenCalledTimes(1)
    api[method].mockResolvedValue({ data: {} })
  })

  it.each(RETRY_CASES)('%s retries server errors twice before failing', async (_l, hook, group, method) => {
    const api = (hoisted as unknown as Record<string, Record<string, ReturnType<typeof vi.fn>>>)[group]
    api[method].mockRejectedValue({ response: { status: 503 } })
    const { result } = renderHook(hook, { wrapper: fastRetryWrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(api[method]).toHaveBeenCalledTimes(3)
    api[method].mockResolvedValue({ data: {} })
  })
})
