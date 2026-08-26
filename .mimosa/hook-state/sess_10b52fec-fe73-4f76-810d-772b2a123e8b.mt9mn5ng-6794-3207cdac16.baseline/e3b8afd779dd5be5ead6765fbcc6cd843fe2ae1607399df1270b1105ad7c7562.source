import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

// Create mock API functions using vi.hoisted
// TanStack Query v5 passes (variables, client, meta, mutationKey) to mutation functions
const { 
  mockContentApi, 
  mockMediaApi, 
  mockWorkflowApi, 
  mockAccountsApi, 
  mockPublishingApi, 
  mockAnalyticsApi, 
  mockAiApi 
} = vi.hoisted(() => {
  return {
    mockContentApi: {
      listPosts: vi.fn(),
      getPost: vi.fn(),
      createPost: vi.fn(),
      updatePost: vi.fn(),
      deletePost: vi.fn(),
      duplicatePost: vi.fn(),
      publishNow: vi.fn(),
      schedulePost: vi.fn(),
      getMedia: vi.fn(),
      uploadMedia: vi.fn(),
      deleteMedia: vi.fn(),
    },
    mockMediaApi: {
      list: vi.fn(),
      upload: vi.fn(),
      delete: vi.fn(),
      generateImage: vi.fn(),
    },
    mockWorkflowApi: {
      listTemplates: vi.fn(),
      getTemplate: vi.fn(),
      createTemplate: vi.fn(),
      updateTemplate: vi.fn(),
      deleteTemplate: vi.fn(),
      generateWorkflow: vi.fn(),
      listWorkflows: vi.fn(),
      getWorkflow: vi.fn(),
      deployWorkflow: vi.fn(),
      undeployWorkflow: vi.fn(),
      deleteWorkflow: vi.fn(),
    },
    mockAccountsApi: {
      list: vi.fn(),
      get: vi.fn(),
      connect: vi.fn(),
      disconnect: vi.fn(),
      refresh: vi.fn(),
      test: vi.fn(),
    },
    mockPublishingApi: {
      listQueue: vi.fn(),
      getQueueItem: vi.fn(),
      retryQueueItem: vi.fn(),
      cancelQueueItem: vi.fn(),
      getHistory: vi.fn(),
    },
    mockAnalyticsApi: {
      getOverview: vi.fn(),
      getPlatformMetrics: vi.fn(),
      getPostAnalytics: vi.fn(),
      getEngagementTrends: vi.fn(),
      getFollowerGrowth: vi.fn(),
      getTopPosts: vi.fn(),
      exportReport: vi.fn(),
    },
    mockAiApi: {
      generateContent: vi.fn(),
      improveContent: vi.fn(),
      generateHashtags: vi.fn(),
      generateImagePrompt: vi.fn(),
      analyzeContent: vi.fn(),
    },
  }
})

// Mock the services/api module
vi.mock('@/services/api', () => ({
  contentApi: mockContentApi,
  mediaApi: mockMediaApi,
  workflowApi: mockWorkflowApi,
  accountsApi: mockAccountsApi,
  publishingApi: mockPublishingApi,
  analyticsApi: mockAnalyticsApi,
  aiApi: mockAiApi,
}))

// Import hooks after mocks
import {
  usePosts,
  usePost,
  useCreatePost,
  useUpdatePost,
  useDeletePost,
  usePublishPost,
  useSchedulePost,
  useMedia,
  useUploadMedia,
  useDeleteMedia,
  useGenerateImage,
  useTemplates,
  useTemplate,
  useCreateTemplate,
  useGenerateWorkflow,
  useWorkflows,
  useDeployWorkflow,
  useAccounts,
  useConnectAccount,
  useDisconnectAccount,
  usePublishQueue,
  useRetryQueueItem,
  useOverviewMetrics,
  usePlatformMetrics,
  usePostAnalytics,
  useTopPosts,
  useGenerateContent,
  useImproveContent,
  useGenerateHashtags,
  useAnalyzeContent,
} from '@/hooks/useQueries'

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  )
}

describe('useQueries hooks', () => {
  const wrapper = createWrapper()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  describe('Content hooks', () => {
    describe('usePosts', () => {
      it('should fetch posts with params', async () => {
        const mockPosts = [
          { id: '1', content_text: 'Post 1', status: 'published' },
          { id: '2', content_text: 'Post 2', status: 'draft' },
        ]
        mockContentApi.listPosts.mockResolvedValue({ data: mockPosts })

        const { result } = renderHook(() => usePosts({ status: 'published', page: 1 }), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockPosts)
        expect(mockContentApi.listPosts).toHaveBeenCalledWith({ status: 'published', page: 1 })
      })

      it('should handle fetch error', async () => {
        mockContentApi.listPosts.mockRejectedValue(new Error('Network error'))

        const { result } = renderHook(() => usePosts(), { wrapper })

        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error).toBeDefined()
      })
    })

    describe('usePost', () => {
      it('should fetch single post by id', async () => {
        const mockPost = { id: '1', content_text: 'Post 1', status: 'published' }
        mockContentApi.getPost.mockResolvedValue({ data: mockPost })

        const { result } = renderHook(() => usePost('1'), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockPost)
        expect(mockContentApi.getPost).toHaveBeenCalledWith('1')
      })

      it('should not fetch when id is empty', async () => {
        const { result } = renderHook(() => usePost(''), { wrapper })

        expect(result.current.isLoading).toBe(false)
        expect(mockContentApi.getPost).not.toHaveBeenCalled()
      })
    })

    describe('useCreatePost', () => {
      it('should create post and invalidate queries', async () => {
        const newPost = { content_text: 'New post', media_ids: [] }
        const createdPost = { id: '1', ...newPost, status: 'draft' }
        mockContentApi.createPost.mockResolvedValue({ data: createdPost })

        const { result } = renderHook(() => useCreatePost(), { wrapper })

        await waitFor(() => {
          result.current.mutate(newPost)
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(createdPost)
        expect(mockContentApi.createPost.mock.calls[0][0]).toEqual(newPost)
      })

      it('should handle create error', async () => {
        mockContentApi.createPost.mockRejectedValue(new Error('Failed to create'))

        const { result } = renderHook(() => useCreatePost(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ content_text: 'Test' })
        })

        await waitFor(() => expect(result.current.isError).toBe(true))
      })
    })

    describe('useUpdatePost', () => {
      it('should update post and invalidate queries', async () => {
        const updatedPost = { id: '1', content_text: 'Updated post' }
        mockContentApi.updatePost.mockResolvedValue({ data: updatedPost })

        const { result } = renderHook(() => useUpdatePost(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ id: '1', data: { content_text: 'Updated post' } })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(updatedPost)
        expect(mockContentApi.updatePost.mock.calls[0][0]).toEqual('1')
        expect(mockContentApi.updatePost.mock.calls[0][1]).toEqual({ content_text: 'Updated post' })
      })
    })

    describe('useDeletePost', () => {
      it('should delete post and invalidate queries', async () => {
        const deleteResult = { success: true }
        mockContentApi.deletePost.mockResolvedValue({ data: deleteResult })

        const { result } = renderHook(() => useDeletePost(), { wrapper })

        await waitFor(() => {
          result.current.mutate('1')
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(deleteResult)
        expect(mockContentApi.deletePost.mock.calls[0][0]).toEqual('1')
      })
    })

    describe('usePublishPost', () => {
      it('should publish post and invalidate queries', async () => {
        const publishResult = { success: true }
        mockContentApi.publishNow.mockResolvedValue({ data: publishResult })

        const { result } = renderHook(() => usePublishPost(), { wrapper })

        await waitFor(() => {
          result.current.mutate('1')
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(publishResult)
        expect(mockContentApi.publishNow.mock.calls[0][0]).toEqual('1')
      })
    })

    describe('useSchedulePost', () => {
      it('should schedule post and invalidate queries', async () => {
        const scheduleResult = { success: true }
        mockContentApi.schedulePost.mockResolvedValue({ data: scheduleResult })

        const { result } = renderHook(() => useSchedulePost(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ id: '1', scheduled_at: '2024-01-01T00:00:00Z' })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(scheduleResult)
        expect(mockContentApi.schedulePost.mock.calls[0][0]).toEqual('1')
        expect(mockContentApi.schedulePost.mock.calls[0][1]).toEqual('2024-01-01T00:00:00Z')
      })
    })
  })

  describe('Media hooks', () => {
    describe('useMedia', () => {
      it('should fetch media with params', async () => {
        const mockMedia = [
          { id: '1', storage_path: '/media/1.jpg', type: 'image' },
          { id: '2', storage_path: '/media/2.mp4', type: 'video' },
        ]
        mockMediaApi.list.mockResolvedValue({ data: mockMedia })

        const { result } = renderHook(() => useMedia({ page: 1, type: 'image' }), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockMedia)
        expect(mockMediaApi.list).toHaveBeenCalledWith({ page: 1, type: 'image' })
      })
    })

    describe('useUploadMedia', () => {
      it('should upload media and invalidate queries', async () => {
        const mockFile = new File(['test'], 'test.jpg', { type: 'image/jpeg' })
        const uploadedMedia = { id: '1', storage_path: '/media/1.jpg', type: 'image' }
        mockMediaApi.upload.mockResolvedValue({ data: uploadedMedia })

        const { result } = renderHook(() => useUploadMedia(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ file: mockFile, alt_text: 'Test', tags: 'test' })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(uploadedMedia)
        expect(mockMediaApi.upload.mock.calls[0][0]).toEqual(mockFile)
        expect(mockMediaApi.upload.mock.calls[0][1]).toEqual('Test')
        expect(mockMediaApi.upload.mock.calls[0][2]).toEqual('test')
      })
    })

    describe('useDeleteMedia', () => {
      it('should delete media and invalidate queries', async () => {
        const deleteResult = { success: true }
        mockMediaApi.delete.mockResolvedValue({ data: deleteResult })

        const { result } = renderHook(() => useDeleteMedia(), { wrapper })

        await waitFor(() => {
          result.current.mutate('1')
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(deleteResult)
        expect(mockMediaApi.delete.mock.calls[0][0]).toEqual('1')
      })
    })

    describe('useGenerateImage', () => {
      it('should generate image', async () => {
        const generatedImage = { id: '1', storage_path: '/media/generated.jpg', type: 'image' }
        mockMediaApi.generateImage.mockResolvedValue({ data: generatedImage })

        const { result } = renderHook(() => useGenerateImage(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ 
            prompt: 'A beautiful sunset',
            options: { width: 1024, height: 1024 }
          })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(generatedImage)
        expect(mockMediaApi.generateImage.mock.calls[0][0]).toEqual('A beautiful sunset')
        expect(mockMediaApi.generateImage.mock.calls[0][1]).toEqual({ width: 1024, height: 1024 })
      })
    })
  })

  describe('Workflow hooks', () => {
    describe('useTemplates', () => {
      it('should fetch templates with category', async () => {
        const mockTemplates = [
          { id: '1', name: 'Template 1', category: 'social' },
          { id: '2', name: 'Template 2', category: 'marketing' },
        ]
        mockWorkflowApi.listTemplates.mockResolvedValue({ data: mockTemplates })

        const { result } = renderHook(() => useTemplates('social'), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockTemplates)
        expect(mockWorkflowApi.listTemplates).toHaveBeenCalledWith('social')
      })

      it('should fetch templates without category', async () => {
        const mockTemplates = [{ id: '1', name: 'Template 1' }]
        mockWorkflowApi.listTemplates.mockResolvedValue({ data: mockTemplates })

        const { result } = renderHook(() => useTemplates(undefined), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(mockWorkflowApi.listTemplates).toHaveBeenCalledWith(undefined)
      })
    })

    describe('useTemplate', () => {
      it('should fetch single template by id', async () => {
        const mockTemplate = { id: '1', name: 'Template 1', prompt_template: 'Test prompt' }
        mockWorkflowApi.getTemplate.mockResolvedValue({ data: mockTemplate })

        const { result } = renderHook(() => useTemplate('1'), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockTemplate)
        expect(mockWorkflowApi.getTemplate).toHaveBeenCalledWith('1')
      })

      it('should not fetch when id is empty', async () => {
        const { result } = renderHook(() => useTemplate(''), { wrapper })

        expect(result.current.isLoading).toBe(false)
        expect(mockWorkflowApi.getTemplate).not.toHaveBeenCalled()
      })
    })

    describe('useCreateTemplate', () => {
      it('should create template and invalidate queries', async () => {
        const newTemplate = { name: 'New Template', prompt_template: 'Test' }
        const createdTemplate = { id: '1', ...newTemplate }
        mockWorkflowApi.createTemplate.mockResolvedValue({ data: createdTemplate })

        const { result } = renderHook(() => useCreateTemplate(), { wrapper })

        await waitFor(() => {
          result.current.mutate(newTemplate)
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(createdTemplate)
        expect(mockWorkflowApi.createTemplate.mock.calls[0][0]).toEqual(newTemplate)
      })
    })

    describe('useGenerateWorkflow', () => {
      it('should generate workflow', async () => {
        const generatedWorkflow = { id: '1', n8n_workflow_json: {} }
        mockWorkflowApi.generateWorkflow.mockResolvedValue({ data: generatedWorkflow })

        const { result } = renderHook(() => useGenerateWorkflow(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ prompt: 'Create a workflow', template_id: '1' })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(generatedWorkflow)
        expect(mockWorkflowApi.generateWorkflow.mock.calls[0][0]).toEqual({ prompt: 'Create a workflow', template_id: '1' })
      })
    })

    describe('useWorkflows', () => {
      it('should fetch workflows with params', async () => {
        const mockWorkflows = [
          { id: '1', name: 'Workflow 1', status: 'deployed' },
          { id: '2', name: 'Workflow 2', status: 'draft' },
        ]
        mockWorkflowApi.listWorkflows.mockResolvedValue({ data: mockWorkflows })

        const { result } = renderHook(() => useWorkflows({ status: 'deployed' }), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockWorkflows)
        expect(mockWorkflowApi.listWorkflows).toHaveBeenCalledWith({ status: 'deployed' })
      })
    })

    describe('useDeployWorkflow', () => {
      it('should deploy workflow and invalidate queries', async () => {
        const deployResult = { success: true }
        mockWorkflowApi.deployWorkflow.mockResolvedValue({ data: deployResult })

        const { result } = renderHook(() => useDeployWorkflow(), { wrapper })

        await waitFor(() => {
          result.current.mutate('1')
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(deployResult)
        expect(mockWorkflowApi.deployWorkflow.mock.calls[0][0]).toEqual('1')
      })
    })
  })

  describe('Accounts hooks', () => {
    describe('useAccounts', () => {
      it('should fetch accounts', async () => {
        const mockAccounts = [
          { id: '1', platform: 'twitter', username: 'user1' },
          { id: '2', platform: 'linkedin', username: 'user2' },
        ]
        mockAccountsApi.list.mockResolvedValue({ data: mockAccounts })

        const { result } = renderHook(() => useAccounts(), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockAccounts)
        expect(mockAccountsApi.list).toHaveBeenCalled()
      })
    })

    describe('useConnectAccount', () => {
      it('should connect account and invalidate queries', async () => {
        const newAccount = { id: '1', platform: 'twitter', username: 'user1' }
        mockAccountsApi.connect.mockResolvedValue({ data: newAccount })

        const { result } = renderHook(() => useConnectAccount(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ platform: 'twitter', teamId: 'team1' })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(newAccount)
        expect(mockAccountsApi.connect.mock.calls[0][0]).toEqual('twitter')
        expect(mockAccountsApi.connect.mock.calls[0][1]).toEqual('team1')
      })
    })

    describe('useDisconnectAccount', () => {
      it('should disconnect account and invalidate queries', async () => {
        const disconnectResult = { success: true }
        mockAccountsApi.disconnect.mockResolvedValue({ data: disconnectResult })

        const { result } = renderHook(() => useDisconnectAccount(), { wrapper })

        await waitFor(() => {
          result.current.mutate('1')
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(disconnectResult)
        expect(mockAccountsApi.disconnect.mock.calls[0][0]).toEqual('1')
      })
    })
  })

  describe('Publishing hooks', () => {
    describe('usePublishQueue', () => {
      it('should fetch publish queue with refetchInterval', async () => {
        const mockQueue = [
          { id: '1', post_id: '1', status: 'pending' },
          { id: '2', post_id: '2', status: 'processing' },
        ]
        mockPublishingApi.listQueue.mockResolvedValue({ data: mockQueue })

        const { result } = renderHook(() => usePublishQueue({ status: 'pending' }), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockQueue)
        expect(mockPublishingApi.listQueue).toHaveBeenCalledWith({ status: 'pending' })
      })
    })

    describe('useRetryQueueItem', () => {
      it('should retry queue item and invalidate queries', async () => {
        const retryResult = { success: true }
        mockPublishingApi.retryQueueItem.mockResolvedValue({ data: retryResult })

        const { result } = renderHook(() => useRetryQueueItem(), { wrapper })

        await waitFor(() => {
          result.current.mutate('1')
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(retryResult)
        expect(mockPublishingApi.retryQueueItem.mock.calls[0][0]).toEqual('1')
      })
    })
  })

  describe('Analytics hooks', () => {
    describe('useOverviewMetrics', () => {
      it('should fetch overview metrics with refetchInterval', async () => {
        const mockMetrics = { total_posts: 100, total_engagement: 5000, total_followers: 10000 }
        mockAnalyticsApi.getOverview.mockResolvedValue({ data: mockMetrics })

        const { result } = renderHook(() => useOverviewMetrics(30), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockMetrics)
        expect(mockAnalyticsApi.getOverview).toHaveBeenCalledWith({ days: 30 })
      })

      it('should fetch overview metrics without days', async () => {
        const mockMetrics = { total_posts: 100 }
        mockAnalyticsApi.getOverview.mockResolvedValue({ data: mockMetrics })

        const { result } = renderHook(() => useOverviewMetrics(undefined), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(mockAnalyticsApi.getOverview).toHaveBeenCalledWith({ days: undefined })
      })
    })

    describe('usePlatformMetrics', () => {
      it('should fetch platform metrics with refetchInterval', async () => {
        const mockMetrics = [
          { platform: 'twitter', followers: 5000, engagement: 1000 },
          { platform: 'linkedin', followers: 3000, engagement: 500 },
        ]
        mockAnalyticsApi.getPlatformMetrics.mockResolvedValue({ data: mockMetrics })

        const { result } = renderHook(() => usePlatformMetrics(30), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockMetrics)
        expect(mockAnalyticsApi.getPlatformMetrics).toHaveBeenCalledWith({ days: 30 })
      })
    })

    describe('usePostAnalytics', () => {
      it('should fetch post analytics', async () => {
        const mockAnalytics = { post_id: '1', views: 1000, likes: 100, comments: 20, shares: 10 }
        mockAnalyticsApi.getPostAnalytics.mockResolvedValue({ data: mockAnalytics })

        const { result } = renderHook(() => usePostAnalytics('1'), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockAnalytics)
        expect(mockAnalyticsApi.getPostAnalytics).toHaveBeenCalledWith('1')
      })

      it('should not fetch when postId is empty', async () => {
        const { result } = renderHook(() => usePostAnalytics(''), { wrapper })

        expect(result.current.isLoading).toBe(false)
        expect(mockAnalyticsApi.getPostAnalytics).not.toHaveBeenCalled()
      })
    })

    describe('useTopPosts', () => {
      it('should fetch top posts', async () => {
        const mockPosts = [
          { id: '1', content_text: 'Top post 1', engagement_count: 1000 },
          { id: '2', content_text: 'Top post 2', engagement_count: 500 },
        ]
        mockAnalyticsApi.getTopPosts.mockResolvedValue({ data: mockPosts })

        const { result } = renderHook(() => useTopPosts(10, 'twitter'), { wrapper })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toEqual(mockPosts)
        expect(mockAnalyticsApi.getTopPosts).toHaveBeenCalledWith({ limit: 10, platform: 'twitter' })
      })
    })
  })

  describe('AI hooks', () => {
    describe('useGenerateContent', () => {
      it('should generate content', async () => {
        const generatedContent = { content: 'Generated content', hashtags: ['#test'] }
        mockAiApi.generateContent.mockResolvedValue({ data: generatedContent })

        const { result } = renderHook(() => useGenerateContent(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ 
            prompt: 'Create a post about AI', 
            platform: 'twitter',
            tone: 'professional'
          })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(generatedContent)
        expect(mockAiApi.generateContent.mock.calls[0][0]).toEqual({
          prompt: 'Create a post about AI',
          platform: 'twitter',
          tone: 'professional',
        })
      })
    })

    describe('useImproveContent', () => {
      it('should improve content', async () => {
        const improvedContent = { content: 'Improved content' }
        mockAiApi.improveContent.mockResolvedValue({ data: improvedContent })

        const { result } = renderHook(() => useImproveContent(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ 
            content: 'Original content', 
            platform: 'twitter',
            instruction: 'Make it more engaging'
          })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(improvedContent)
        expect(mockAiApi.improveContent.mock.calls[0][0]).toEqual({
          content: 'Original content',
          platform: 'twitter',
          instruction: 'Make it more engaging',
        })
      })
    })

    describe('useGenerateHashtags', () => {
      it('should generate hashtags', async () => {
        const hashtags = ['#ai', '#tech', '#innovation']
        mockAiApi.generateHashtags.mockResolvedValue({ data: { hashtags } })

        const { result } = renderHook(() => useGenerateHashtags(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ content: 'AI is transforming the world', platform: 'twitter', count: 5 })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual({ hashtags })
        expect(mockAiApi.generateHashtags.mock.calls[0][0]).toEqual({
          content: 'AI is transforming the world',
          platform: 'twitter',
          count: 5,
        })
      })
    })

    describe('useAnalyzeContent', () => {
      it('should analyze content', async () => {
        const analysis = { sentiment: 'positive', readability: 85, engagement_potential: 'high' }
        mockAiApi.analyzeContent.mockResolvedValue({ data: analysis })

        const { result } = renderHook(() => useAnalyzeContent(), { wrapper })

        await waitFor(() => {
          result.current.mutate({ content: 'Great content!', platform: 'twitter' })
        })

        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.data).toEqual(analysis)
        expect(mockAiApi.analyzeContent.mock.calls[0][0]).toEqual({ content: 'Great content!', platform: 'twitter' })
      })
    })
  })
})