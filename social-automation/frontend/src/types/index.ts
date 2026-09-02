export interface User {
  id: string
  email: string
  name: string | null
  avatar_url: string | null
  timezone: string
  two_factor_enabled: boolean
  created_at: string
}

export interface Team {
  id: string
  name: string
  owner_id: string
  created_at: string
  updated_at: string
}

export interface TeamMember {
  id: string
  team_id: string
  user_id: string
  role: 'owner' | 'admin' | 'editor' | 'viewer'
  created_at: string
}

export interface SocialAccount {
  id: string
  team_id: string
  platform: 'linkedin' | 'twitter' | 'instagram' | 'facebook' | 'threads' | 'tiktok'
  account_id: string
  username: string | null
  display_name: string | null
  avatar_url: string | null
  status: 'active' | 'expired' | 'error' | 'disconnected'
  scopes: string[]
  token_expires_at: string | null
  last_sync_at: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  account_type: 'person' | 'organization' | 'page' | 'business' | 'creator' | 'user' | string
  is_business: boolean
  parent_account_id: string | null
  meta_data?: Record<string, unknown>
}

export interface Post {
  id: string
  team_id: string
  user_id: string | null
  status: 'draft' | 'review' | 'approved' | 'scheduled' | 'publishing' | 'published' | 'failed' | 'archived'
  content_text: string | null
  media_ids: string[]
  platform_specific: Record<string, unknown>
  hashtags: string[]
  mention_accounts: string[]
  link_url: string | null
  link_preview_override: Record<string, unknown> | null
  scheduled_at: string | null
  published_at: string | null
  failed_at: string | null
  failure_reason: string | null
  workflow_id: string | null
  workflow_run_id: string | null
  metadata: Record<string, unknown>
  pillar_id?: string | null
  content_brief_id?: string | null
  comments?: Array<{ id: string; author_name: string; body: string; action?: string; created_at: string }>
  created_at: string
  updated_at: string
  targets?: PostTarget[]
}

export interface PostTarget {
  post_id: string
  social_account_id: string
  platform?: string
  username?: string
  platform_post_id: string | null
  platform_url: string | null
  status: 'pending' | 'published' | 'failed'
  error_message: string | null
  published_at: string | null
  social_account?: SocialAccount
}

export interface MediaAsset {
  id: string
  team_id: string
  user_id: string | null
  filename: string | null
  mime_type: string | null
  size_bytes: number | null
  storage_path: string
  width: number | null
  height: number | null
  duration_seconds: number | null
  alt_text: string | null
  tags: string[]
  ai_caption: string | null
  source: 'upload' | 'comfyui' | 'url' | 'ai-generated' | 'ai-logo' | 'ai-favicon' | 'n8n-cf-pipe'
  generation_prompt: string | null
  comfyui_workflow_json: Record<string, unknown> | null
  created_at: string
}

export interface PromptTemplate {
  id: string
  team_id: string
  user_id: string | null
  name: string
  description: string | null
  prompt_template: string
  n8n_workflow_json: Record<string, unknown>
  category: string | null
  tags: string[]
  is_public: boolean
  usage_count: number
  created_at: string
  updated_at: string
}

export interface GeneratedWorkflow {
  id: string
  team_id: string
  user_id: string | null
  template_id: string | null
  prompt_text: string
  n8n_workflow_json: Record<string, unknown>
  variables_used: Record<string, unknown>
  status: 'draft' | 'deployed' | 'active' | 'archived'
  n8n_workflow_id: string | null
  created_at: string
  updated_at: string
}

export interface PublishQueueItem {
  id: string
  team_id: string
  post_id: string
  social_account_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'retrying'
  attempt: number
  max_attempts: number
  scheduled_at: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  response_data: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface AnalyticsEvent {
  id: string
  team_id: string
  post_id: string | null
  social_account_id: string | null
  event_type: 'impression' | 'click' | 'like' | 'comment' | 'share' | 'reach' | 'engagement' | 'follower_change'
  platform: string
  platform_event_id: string | null
  value: number
  metadata: Record<string, unknown>
  occurred_at: string
  created_at: string
}

export interface OverviewMetrics {
  total_posts: number
  published_posts: number
  scheduled_posts: number
  draft_posts: number
  failed_posts: number
  connected_accounts: number
  total_followers: number
  total_engagement: number
}

export interface PlatformMetrics {
  platform: string
  posts_count: number
  published_count: number
  scheduled_count: number
  total_impressions: number
  total_engagement: number
  /** Ratio 0–1 from API */
  engagement_rate: number
}

/** Top posts from GET /analytics/top-posts */
export interface TopPost {
  post_id: string
  content_text: string | null
  platform: string
  impressions: number
  engagement: number
  engagement_rate: number
  published_at: string | null
}

/** Daily engagement from GET /analytics/engagement */
export interface EngagementPoint {
  date: string
  likes: number
  comments: number
  shares: number
  clicks: number
  total: number
}

/** Per-target metrics from GET /analytics/posts/{id}/metrics */
export interface PostAnalytics {
  post_id: string
  platform: string
  impressions: number
  clicks: number
  likes: number
  comments: number
  shares: number
  engagement_rate: number
}

export interface AuthState {
  user: User | null
  access_token: string | null
  refresh_token: string | null
  isAuthenticated: boolean
  isLoading: boolean
}

export interface LoginCredentials {
  email: string
  password: string
}

export interface RegisterData {
  email: string
  password: string
  name: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface ApiError {
  detail: string | Array<{
    loc: (string | number)[]
    msg: string
    type: string
  }>
}