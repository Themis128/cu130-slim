import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  contentApi,
  mediaApi,
  workflowApi,
  accountsApi,
  publishingApi,
  analyticsApi,
  aiApi,
} from '@/services/api'
import type { Post, MediaAsset, PromptTemplate, GeneratedWorkflow, SocialAccount } from '@/types'
import toast from 'react-hot-toast'

// Content hooks
export function usePosts(params?: { status?: string; page?: number; page_size?: number }) {
  return useQuery({
    queryKey: ['posts', params],
    queryFn: () => contentApi.listPosts(params),
    select: (response) => response.data,
  })
}

export function usePost(id: string) {
  return useQuery({
    queryKey: ['post', id],
    queryFn: () => contentApi.getPost(id),
    select: (response) => response.data,
    enabled: !!id,
  })
}

export function useCreatePost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: contentApi.createPost,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      toast.success('Post created')
    },
    onError: (error: unknown) => {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Failed to create post')
    },
  })
}

export function useUpdatePost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Post> }) => contentApi.updatePost(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      queryClient.invalidateQueries({ queryKey: ['post', id] })
      toast.success('Post updated')
    },
  })
}

export function useDeletePost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: contentApi.deletePost,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      toast.success('Post deleted')
    },
  })
}

export function usePublishPost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: contentApi.publishNow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      toast.success('Post published')
    },
  })
}

export function useSchedulePost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, scheduled_at }: { id: string; scheduled_at: string }) => contentApi.schedulePost(id, scheduled_at),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      toast.success('Post scheduled')
    },
  })
}

// Media hooks
export function useMedia(params?: { page?: number; page_size?: number; type?: string }) {
  return useQuery({
    queryKey: ['media', params],
    queryFn: () => mediaApi.list(params),
    select: (response) => response.data,
  })
}

export function useUploadMedia() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, alt_text, tags }: { file: File; alt_text?: string; tags?: string }) =>
      mediaApi.upload(file, alt_text, tags),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media'] })
      toast.success('Media uploaded')
    },
  })
}

export function useDeleteMedia() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: mediaApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media'] })
      toast.success('Media deleted')
    },
  })
}

export function useGenerateImage() {
  return useMutation({
    mutationFn: mediaApi.generateImage,
    onSuccess: () => {
      toast.success('Image generated')
    },
  })
}

// Workflow hooks
export function useTemplates(category?: string) {
  return useQuery({
    queryKey: ['templates', category],
    queryFn: () => workflowApi.listTemplates(category),
    select: (response) => response.data,
  })
}

export function useTemplate(id: string) {
  return useQuery({
    queryKey: ['template', id],
    queryFn: () => workflowApi.getTemplate(id),
    select: (response) => response.data,
    enabled: !!id,
  })
}

export function useCreateTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: workflowApi.createTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['templates'] })
      toast.success('Template created')
    },
  })
}

export function useGenerateWorkflow() {
  return useMutation({
    mutationFn: workflowApi.generateWorkflow,
    onSuccess: () => {
      toast.success('Workflow generated')
    },
  })
}

export function useWorkflows(params?: { status?: string }) {
  return useQuery({
    queryKey: ['workflows', params],
    queryFn: () => workflowApi.listWorkflows(params),
    select: (response) => response.data,
  })
}

export function useDeployWorkflow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: workflowApi.deployWorkflow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
      toast.success('Workflow deployed')
    },
  })
}

// Accounts hooks
export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
    select: (response) => response.data,
  })
}

export function useConnectAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ platform, teamId }: { platform: string; teamId: string }) => accountsApi.connect(platform, teamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      toast.success('Account connected')
    },
  })
}

export function useDisconnectAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: accountsApi.disconnect,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      toast.success('Account disconnected')
    },
  })
}

// Publishing hooks
export function usePublishQueue(params?: { status?: string; page?: number; page_size?: number }) {
  return useQuery({
    queryKey: ['publish-queue', params],
    queryFn: () => publishingApi.listQueue(params),
    select: (response) => response.data,
    refetchInterval: 5000,
  })
}

export function useRetryQueueItem() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: publishingApi.retryQueueItem,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['publish-queue'] })
      toast.success('Retrying...')
    },
  })
}

// Analytics hooks
export function useOverviewMetrics(days?: number) {
  return useQuery({
    queryKey: ['analytics', 'overview', days],
    queryFn: () => analyticsApi.getOverview({ days }),
    select: (response) => response.data,
    refetchInterval: 30000,
  })
}

export function usePlatformMetrics(days?: number) {
  return useQuery({
    queryKey: ['analytics', 'platforms', days],
    queryFn: () => analyticsApi.getPlatformMetrics({ days }),
    select: (response) => response.data,
    refetchInterval: 30000,
  })
}

export function usePostAnalytics(postId: string) {
  return useQuery({
    queryKey: ['analytics', 'post', postId],
    queryFn: () => analyticsApi.getPostAnalytics(postId),
    select: (response) => response.data,
    enabled: !!postId,
  })
}

export function useTopPosts(limit?: number, platform?: string) {
  return useQuery({
    queryKey: ['analytics', 'top-posts', limit, platform],
    queryFn: () => analyticsApi.getTopPosts({ limit, platform }),
    select: (response) => response.data,
  })
}

// AI hooks
export function useGenerateContent() {
  return useMutation({
    mutationFn: aiApi.generateContent,
    onSuccess: () => {
      toast.success('Content generated')
    },
  })
}

export function useImproveContent() {
  return useMutation({
    mutationFn: aiApi.improveContent,
    onSuccess: () => {
      toast.success('Content improved')
    },
  })
}

export function useGenerateHashtags() {
  return useMutation({
    mutationFn: aiApi.generateHashtags,
    onSuccess: () => {
      toast.success('Hashtags generated')
    },
  })
}

export function useAnalyzeContent() {
  return useMutation({
    mutationFn: aiApi.analyzeContent,
    onSuccess: () => {
      toast.success('Analysis complete')
    },
  })
}