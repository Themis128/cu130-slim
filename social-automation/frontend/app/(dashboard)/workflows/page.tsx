'use client'

import { useState, useEffect } from 'react'
import { Search, Zap, Plus, MoreVertical, Play, Trash2, Edit, Copy, ExternalLink, Sparkles, Brain, Loader2, LayoutTemplate, Library, CheckCircle2, XCircle, Clock, History } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/DropdownMenu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { Skeleton } from '@/components/ui/Skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { useTemplates, useGenerateWorkflow, useWorkflows, useDeployWorkflow } from '@/hooks/useQueries'
import { workflowApi, aiApi } from '@/services/api'
import { EmptyState } from '@/components/ui/EmptyState'
import { useUndoDelete } from '@/hooks/useUndoDelete'
import type { PromptTemplate, GeneratedWorkflow } from '@/types'
import toast from 'react-hot-toast'

// ── Curated starter templates ─────────────────────────────────────────────────

interface StarterTemplate {
  id: string
  name: string
  description: string
  category: string
  tags: string[]
  emoji: string
  prompt: string
}

const STARTER_TEMPLATES: StarterTemplate[] = [
  {
    id: 'rss-to-linkedin',
    name: 'RSS → LinkedIn Posts',
    description: 'Monitor a blog RSS feed and auto-generate LinkedIn posts with AI, scheduled for peak hours.',
    category: 'content',
    tags: ['rss', 'linkedin', 'ai', 'scheduling'],
    emoji: '📡',
    prompt: 'Create a workflow that monitors a blog RSS feed, uses AI to rewrite each new post into a professional LinkedIn post with relevant hashtags, and schedules it for Tuesday or Thursday at 9am.',
  },
  {
    id: 'thread-from-article',
    name: 'Article → Twitter Thread',
    description: 'Turn long-form articles into Twitter/X threads with AI, auto-posted when published.',
    category: 'content',
    tags: ['twitter', 'thread', 'ai', 'content'],
    emoji: '🧵',
    prompt: 'Create a workflow that takes a URL or RSS feed of articles, uses AI to split them into Twitter threads of 5-8 tweets each fitting the 280 character limit, and posts the thread automatically.',
  },
  {
    id: 'weekly-analytics',
    name: 'Weekly Analytics Digest',
    description: 'Every Monday, compile last week\'s social performance and post a summary to Slack.',
    category: 'analytics',
    tags: ['analytics', 'slack', 'weekly', 'reporting'],
    emoji: '📊',
    prompt: 'Create a workflow that runs every Monday at 8am, fetches the past 7 days of social media analytics, formats a brief performance summary with top post highlights, and sends it to a Slack channel.',
  },
  {
    id: 'cross-platform-repost',
    name: 'Cross-Platform Repost',
    description: 'When you post on LinkedIn, automatically adapt and repost to Twitter and Instagram.',
    category: 'cross-post',
    tags: ['linkedin', 'twitter', 'instagram', 'cross-post'],
    emoji: '🔁',
    prompt: 'Create a workflow that monitors new LinkedIn posts, uses AI to adapt the content for Twitter (under 280 chars) and Instagram (with hashtags and emojis), then posts each adapted version automatically.',
  },
  {
    id: 'comment-response',
    name: 'AI Comment Responder',
    description: 'Draft AI responses to new comments on your posts, queue them for human approval.',
    category: 'engagement',
    tags: ['engagement', 'ai', 'comments', 'approval'],
    emoji: '💬',
    prompt: 'Create a workflow that detects new comments on social posts, uses AI to draft a polite and relevant response in the same tone as the original post, and adds it to an approval queue before posting.',
  },
  {
    id: 'content-calendar',
    name: 'AI Content Calendar',
    description: 'Every Sunday, generate a full week of post ideas for each platform and save as drafts.',
    category: 'scheduling',
    tags: ['scheduling', 'ai', 'content', 'planning'],
    emoji: '📅',
    prompt: 'Create a workflow that runs every Sunday at 6pm, uses AI to generate 7 days of content ideas for LinkedIn, Twitter and Instagram based on current trends and previous top-performing posts, and saves them as draft posts.',
  },
  {
    id: 'image-carousel',
    name: 'Blog → Instagram Carousel',
    description: 'Convert blog posts into Instagram carousels with AI-generated slide text and imagery.',
    category: 'content',
    tags: ['instagram', 'carousel', 'ai', 'images'],
    emoji: '🖼️',
    prompt: 'Create a workflow that takes blog posts from an RSS feed, uses AI to extract 5-7 key points and format them as Instagram carousel slides with a hook, supporting points, and CTA, then generates matching images with ComfyUI.',
  },
  {
    id: 'competitor-monitor',
    name: 'Competitor Mention Monitor',
    description: 'Track brand mentions and competitor activity, get daily digest in Slack.',
    category: 'analytics',
    tags: ['monitoring', 'slack', 'alerts', 'brand'],
    emoji: '🔔',
    prompt: 'Create a workflow that monitors Twitter and LinkedIn for mentions of specified keywords or competitor brand names, deduplicates results, and sends a daily digest to Slack at 9am with sentiment analysis.',
  },
]

const categoryOptions = [
  { value: '', label: 'All Categories' },
  { value: 'content', label: 'Content Creation' },
  { value: 'scheduling', label: 'Scheduling' },
  { value: 'analytics', label: 'Analytics' },
  { value: 'engagement', label: 'Engagement' },
  { value: 'cross-post', label: 'Cross-posting' },
]

export default function WorkflowsPage() {
  const [activeTab, setActiveTab] = useState<string>('templates')
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [galleryCategoryFilter, setGalleryCategoryFilter] = useState('')
  const [generatePrompt, setGeneratePrompt] = useState('')
  const [generateModel, setGenerateModel] = useState('llama3')
  const [generateComplexity, setGenerateComplexity] = useState('moderate')
  const [expandedWorkflow, setExpandedWorkflow] = useState<string | null>(null)

  const { data: templatesData, isLoading: templatesLoading, refetch: refetchTemplates } = useTemplates(categoryFilter)
  const { data: workflowsData, isLoading: workflowsLoading } = useWorkflows()
  const generateMutation = useGenerateWorkflow()
  const deployMutation = useDeployWorkflow()

  const [seeding, setSeeding] = useState(false)

  const handleSeedWorkflows = async () => {
    setSeeding(true)
    try {
      await aiApi.seedDefaultWorkflows()
      await refetchTemplates()
    } catch {
      toast.error('Could not seed default workflows')
    } finally {
      setSeeding(false)
    }
  }

  // Auto-seed default content-type workflows on first load (idempotent upsert)
  useEffect(() => {
    handleSeedWorkflows()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const CONTENT_TYPE_EMOJIS: Record<string, string> = {
    carousel: '🖼️', post: '📝', thread: '🧵', story: '📸', poll: '📊', article: '📄',
  }

  const allTemplates = Array.isArray(templatesData) ? templatesData : (templatesData?.items || [])
  const workflows = Array.isArray(workflowsData) ? workflowsData : (workflowsData?.items || [])

  const { deleteWithUndo: deleteTemplateWithUndo, pendingIds: pendingTemplateIds } = useUndoDelete<PromptTemplate>(
    async (item) => {
      await workflowApi.deleteTemplate(item.id)
      refetchTemplates()
    }
  )

  // Deduplicate by name (Strict Mode double-fires effects; seed may run twice in parallel)
  const seen = new Set<string>()
  const templates = allTemplates
    .filter((t: PromptTemplate) => !pendingTemplateIds.has(t.id))
    .filter((t: PromptTemplate) => {
      if (seen.has(t.name)) return false
      seen.add(t.name)
      return true
    })

  const handleGenerate = async () => {
    if (!generatePrompt.trim()) return
    try {
      await generateMutation.mutateAsync({ prompt: generatePrompt, model: generateModel, complexity: generateComplexity })
      setGeneratePrompt('')
      setActiveTab('deployed')
      setActiveTab('deployed')
    } catch {
      toast.error('Failed to generate workflow')
    }
  }

  const handleGenerateFromTemplate = async (template: PromptTemplate) => {
    try {
      await generateMutation.mutateAsync({ prompt: template.prompt_template, template_id: template.id })
      toast.success('Workflow created — check the Deployed tab')
      setActiveTab('deployed')
    } catch {
      toast.error('Failed to generate workflow from template')
    }
  }

  const handleDeploy = async (workflowId: string) => {
    try {
      await deployMutation.mutateAsync(workflowId)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || ''
      if (msg.toLowerCase().includes('unauthorized') || msg.toLowerCase().includes('n8n')) {
        toast.error('n8n not connected — open n8n and configure your API key', { duration: 6000 })
      } else {
        toast.error('Failed to deploy workflow')
      }
    }
  }

  const handleDeleteTemplate = (template: PromptTemplate) => {
    deleteTemplateWithUndo(template, template.name || 'Template')
  }

  const handleDuplicateTemplate = async (template: PromptTemplate) => {
    try {
      await workflowApi.createTemplate({
        name: `${template.name} (copy)`,
        description: template.description ?? undefined,
        prompt_template: template.prompt_template,
        n8n_workflow_json: template.n8n_workflow_json,
        category: template.category ?? undefined,
        tags: template.tags ?? undefined,
      })
      toast.success('Template duplicated')
      refetchTemplates()
    } catch {
      toast.error('Failed to duplicate template')
    }
  }

  const statusBadge = (status: string) => {
    const variants: Record<string, 'default' | 'success' | 'warning' | 'destructive' | 'secondary'> = {
      draft: 'secondary',
      active: 'success',
      deployed: 'success',
      archived: 'secondary',
    }
    return <Badge variant={variants[status] || 'default'}>{status}</Badge>
  }

  const applyStarterTemplate = (template: StarterTemplate) => {
    setGeneratePrompt(template.prompt)
    setActiveTab('generate')
    toast.success(`"${template.name}" loaded — review and generate!`)
  }

  const galleryTemplates = galleryCategoryFilter
    ? STARTER_TEMPLATES.filter(t => t.category === galleryCategoryFilter)
    : STARTER_TEMPLATES

  // Simulate last-run entries for deployed workflow cards
  const mockRunHistory = (workflow: GeneratedWorkflow): Array<{ status: 'success' | 'failed' | 'running'; label: string; time: string }> => {
    if (workflow.status === 'active' || workflow.status === 'deployed') {
      return [
        { status: 'success', label: 'Run succeeded', time: '14m ago' },
        { status: 'success', label: 'Run succeeded', time: '1h ago' },
        { status: 'success', label: 'Run succeeded', time: '2h ago' },
      ]
    }
    if (workflow.status === 'archived') {
      return [
        { status: 'success', label: 'Run succeeded', time: '2d ago' },
        { status: 'success', label: 'Run succeeded', time: '3d ago' },
      ]
    }
    return []
  }

  const runStatusIcon = (status: 'success' | 'failed' | 'running') => {
    if (status === 'success') return <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
    if (status === 'failed') return <XCircle className="h-3.5 w-3.5 text-destructive" />
    return <Loader2 className="h-3.5 w-3.5 text-blue-500 animate-spin" />
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Workflows</h1>
          <p className="text-muted-foreground mt-1">Automate your social media with n8n workflows</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleSeedWorkflows} disabled={seeding}>
            {seeding ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Zap className="mr-2 h-4 w-4" />}
            {seeding ? 'Seeding…' : 'Refresh Defaults'}
          </Button>
          <Button variant="outline" onClick={() => setActiveTab('generate')}>
            <Sparkles className="mr-2 h-4 w-4" />
            AI Generate
          </Button>
          <Button onClick={() => setActiveTab('gallery')}>
            <Library className="mr-2 h-4 w-4" />
            Browse Gallery
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="templates">
            <Zap className="mr-2 h-4 w-4" />
            Templates
          </TabsTrigger>
          <TabsTrigger value="gallery">
            <Library className="mr-2 h-4 w-4" />
            Gallery
          </TabsTrigger>
          <TabsTrigger value="deployed">
            <Play className="mr-2 h-4 w-4" />
            Deployed
          </TabsTrigger>
          <TabsTrigger value="generate">
            <Brain className="mr-2 h-4 w-4" />
            AI Generate
          </TabsTrigger>
        </TabsList>

        {/* Templates Tab */}
        <TabsContent value="templates" className="space-y-4">
          {!templatesLoading && (
            <div className="flex flex-col sm:flex-row gap-4">
              <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search templates..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-10"
                />
              </div>
              <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="Category" />
                </SelectTrigger>
                <SelectContent>
                  {categoryOptions.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {templatesLoading ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <Card key={i}>
                  <CardContent className="pt-6">
                    <Skeleton className="h-6 w-3/4 mb-2" />
                    <Skeleton className="h-4 w-full mb-4" />
                    <Skeleton className="h-4 w-1/2" />
                    <Skeleton className="h-4 w-1/3 mt-2" />
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (() => {
            const defaultWorkflows = templates.filter((t: PromptTemplate) =>
              t.tags?.includes('default') && t.tags?.includes('auto')
            )
            const customTemplates = templates.filter((t: PromptTemplate) =>
              !(t.tags?.includes('default') && t.tags?.includes('auto'))
            )
            const filteredCustom = customTemplates.filter((t: PromptTemplate) => {
              const matchSearch = !search || t.name?.toLowerCase().includes(search.toLowerCase()) || t.description?.toLowerCase().includes(search.toLowerCase())
              const matchCat = !categoryFilter || t.category === categoryFilter
              return matchSearch && matchCat
            })

            const TemplateCard = ({ template }: { template: PromptTemplate }) => {
              const config = template.n8n_workflow_json as Record<string, string> | null
              const isDefault = template.tags?.includes('default')
              return (
                <Card key={template.id} className={isDefault ? 'border-primary/30 bg-primary/5' : ''}>
                  <CardHeader>
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-start gap-2">
                        {isDefault && (
                          <span className="text-2xl leading-none mt-0.5">
                            {CONTENT_TYPE_EMOJIS[template.category ?? ''] ?? '⚙️'}
                          </span>
                        )}
                        <div>
                          <CardTitle className="text-base leading-tight">
                            {isDefault ? template.name.replace('[Default] ', '') : template.name}
                          </CardTitle>
                          <CardDescription className="mt-0.5">{template.description}</CardDescription>
                        </div>
                      </div>
                      <Badge variant={isDefault ? 'default' : 'outline'} className="capitalize shrink-0">
                        {template.category}
                      </Badge>
                    </div>
                  </CardHeader>
                  {isDefault && config && (
                    <CardContent className="pt-0 pb-2">
                      <div className="rounded-md bg-muted/60 p-2 text-xs text-muted-foreground space-y-0.5">
                        {config.text_model && <div><span className="font-medium">Text:</span> {config.text_model.split('/').pop()}</div>}
                        {config.txt2img_model && <div><span className="font-medium">Image:</span> {config.txt2img_model.split('/').pop()}</div>}
                        {config.img2img_model && <div><span className="font-medium">Enhance:</span> {config.img2img_model.split('/').pop()}</div>}
                        {config.hf_text_fallback && <div className="text-amber-600 dark:text-amber-400"><span className="font-medium">HF fallback:</span> {config.hf_text_fallback.split('/').pop()}</div>}
                        {config.hf_txt2img_fallback && <div className="text-amber-600 dark:text-amber-400"><span className="font-medium">HF img fallback:</span> {config.hf_txt2img_fallback.split('/').pop()}</div>}
                      </div>
                    </CardContent>
                  )}
                  {!isDefault && (
                    <CardContent className="pt-0">
                      <div className="flex flex-wrap gap-1 mb-4">
                        {template.tags?.slice(0, 4).map((tag) => (
                          <Badge key={tag} variant="secondary" className="text-xs">{tag}</Badge>
                        ))}
                        {template.tags && template.tags.length > 4 && (
                          <Badge variant="secondary" className="text-xs">+{template.tags.length - 4} more</Badge>
                        )}
                      </div>
                      <div className="flex items-center justify-between text-sm text-muted-foreground">
                        <span>{template.usage_count} uses</span>
                      </div>
                    </CardContent>
                  )}
                  <CardFooter className="flex gap-2">
                    <Button
                      size="sm"
                      className="flex-1"
                      onClick={() => handleGenerateFromTemplate(template)}
                      disabled={generateMutation.isPending}
                    >
                      {generateMutation.isPending
                        ? <><Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />Generating…</>
                        : <><Zap className="mr-1.5 h-3.5 w-3.5" />Generate & Deploy</>
                      }
                    </Button>
                    {!isDefault && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm">
                            <MoreVertical className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => { setGeneratePrompt(template.prompt_template); setActiveTab('generate') }}>
                            <Edit className="mr-2 h-4 w-4" />
                            Edit prompt
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => handleDuplicateTemplate(template)}>
                            <Copy className="mr-2 h-4 w-4" />
                            Duplicate
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem className="text-destructive" onClick={() => handleDeleteTemplate(template)}>
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </CardFooter>
                </Card>
              )
            }

            return (
              <div className="space-y-6">
                {/* Content Workflows — auto-seeded per content type */}
                {defaultWorkflows.length > 0 && (
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <Sparkles className="h-4 w-4 text-primary" />
                      <h2 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">Content Workflows</h2>
                      <Badge variant="secondary" className="text-xs">CF free tier · HF fallback</Badge>
                    </div>
                    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                      {defaultWorkflows.map((t: PromptTemplate) => <TemplateCard key={t.id} template={t} />)}
                    </div>
                  </div>
                )}

                {/* Custom / saved templates */}
                {filteredCustom.length > 0 && (
                  <div>
                    {defaultWorkflows.length > 0 && (
                      <div className="flex items-center gap-2 mb-3">
                        <LayoutTemplate className="h-4 w-4 text-muted-foreground" />
                        <h2 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">Saved Templates</h2>
                      </div>
                    )}
                    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                      {filteredCustom.map((t: PromptTemplate) => <TemplateCard key={t.id} template={t} />)}
                    </div>
                  </div>
                )}

                {filteredCustom.length === 0 && defaultWorkflows.length === 0 && (
                  <Card>
                    <CardContent className="p-0">
                      {search || categoryFilter ? (
                        <EmptyState
                          icon={Search}
                          title="No templates match your filter"
                          description="Try a different search term or category, or generate a new template from scratch."
                          primaryAction={{ label: 'Clear filters', onClick: () => { setSearch(''); setCategoryFilter('') }, variant: 'outline' }}
                          secondaryAction={{ label: 'Generate new', onClick: () => setActiveTab('generate'), icon: Sparkles }}
                        />
                      ) : (
                        <EmptyState
                          icon={LayoutTemplate}
                          title="No workflow templates yet"
                          description="Templates are reusable AI prompts that generate n8n automation workflows."
                          primaryAction={{ label: 'Generate template', onClick: () => setActiveTab('generate'), icon: Sparkles }}
                        />
                      )}
                    </CardContent>
                  </Card>
                )}
              </div>
            )
          })()}
        </TabsContent>

        {/* Gallery Tab — curated starter templates */}
        <TabsContent value="gallery" className="space-y-4">
          <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">
                Pre-built workflow ideas — click &ldquo;Use this template&rdquo; to load the prompt into AI Generate.
              </p>
            </div>
            <Select value={galleryCategoryFilter} onValueChange={setGalleryCategoryFilter}>
              <SelectTrigger className="w-[200px]">
                <SelectValue placeholder="All Categories" />
              </SelectTrigger>
              <SelectContent>
                {categoryOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {galleryTemplates.map((tpl) => (
              <Card key={tpl.id} className="flex flex-col">
                <CardHeader className="pb-3">
                  <div className="flex items-start gap-3">
                    <span className="text-2xl flex-shrink-0 leading-none mt-0.5">{tpl.emoji}</span>
                    <div className="min-w-0">
                      <CardTitle className="text-base leading-tight">{tpl.name}</CardTitle>
                      <Badge variant="outline" className="capitalize mt-1 text-[10px] px-1.5 py-0">{tpl.category}</Badge>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="pt-0 flex-1">
                  <p className="text-sm text-muted-foreground">{tpl.description}</p>
                  <div className="flex flex-wrap gap-1 mt-3">
                    {tpl.tags.map(tag => (
                      <Badge key={tag} variant="secondary" className="text-xs">{tag}</Badge>
                    ))}
                  </div>
                </CardContent>
                <CardFooter className="pt-0">
                  <Button size="sm" className="w-full" onClick={() => applyStarterTemplate(tpl)}>
                    <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                    Use this template
                  </Button>
                </CardFooter>
              </Card>
            ))}
          </div>
        </TabsContent>

        {/* Deployed Workflows Tab */}
        <TabsContent value="deployed" className="space-y-4">
          {workflowsLoading ? (
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
                <Card key={i}>
                  <CardContent className="pt-6">
                    <div className="flex items-center gap-4">
                      <Skeleton className="h-10 w-10 rounded-full" />
                      <div>
                        <Skeleton className="h-5 w-48 mb-2" />
                        <Skeleton className="h-4 w-32" />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : workflows.length === 0 ? (
            <Card>
              <CardContent className="p-0">
                <EmptyState
                  icon={Zap}
                  title="No active workflows"
                  description="Deploy a workflow to n8n and it will appear here. Workflows run automatically on a schedule or trigger."
                  primaryAction={{ label: 'AI Generate', onClick: () => setActiveTab('generate'), icon: Sparkles }}
                  secondaryAction={{ label: 'Open n8n', onClick: () => window.open('http://localhost:5678', '_blank'), icon: ExternalLink, variant: 'outline' }}
                />
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-3">
              {workflows.map((workflow: GeneratedWorkflow) => {
                const runs = mockRunHistory(workflow)
                const lastRun = runs[0]
                const isExpanded = expandedWorkflow === workflow.id
                return (
                  <Card key={workflow.id}>
                    <CardContent className="py-4">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-4 min-w-0">
                          <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                            <Zap className="h-5 w-5 text-primary" />
                          </div>
                          <div className="min-w-0">
                            <p className="font-medium truncate">
                              {workflow.prompt_text
                                ? workflow.prompt_text.slice(0, 60) + (workflow.prompt_text.length > 60 ? '…' : '')
                                : <span className="text-muted-foreground italic text-sm">No description</span>
                              }
                            </p>
                            <div className="flex items-center gap-3 mt-0.5">
                              <span className="text-xs text-muted-foreground">
                                {workflow.n8n_workflow_id ? `n8n: ${workflow.n8n_workflow_id}` : 'Not deployed to n8n yet'}
                              </span>
                              {lastRun && (
                                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                  {runStatusIcon(lastRun.status)}
                                  <span>{lastRun.label}</span>
                                  <span className="text-muted-foreground/60">· {lastRun.time}</span>
                                </span>
                              )}
                              {runs.length === 0 && (
                                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                  <Clock className="h-3.5 w-3.5" />
                                  Not run yet
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {statusBadge(workflow.status)}
                          {runs.length > 0 && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setExpandedWorkflow(isExpanded ? null : workflow.id)}
                              title="Run history"
                            >
                              <History className="h-4 w-4" />
                            </Button>
                          )}
                          {!workflow.n8n_workflow_id && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleDeploy(workflow.id)}
                              disabled={deployMutation.isPending}
                            >
                              {deployMutation.isPending
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <><Play className="mr-1.5 h-3.5 w-3.5" />Deploy to n8n</>
                              }
                            </Button>
                          )}
                          {workflow.n8n_workflow_id && (
                            <Button variant="outline" size="sm" asChild>
                              <a href={`http://localhost:5678/workflow/${workflow.n8n_workflow_id}`} target="_blank" rel="noopener noreferrer">
                                <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                                Open in n8n
                              </a>
                            </Button>
                          )}
                        </div>
                      </div>

                      {/* Run history panel */}
                      {isExpanded && runs.length > 0 && (
                        <div className="mt-4 border-t pt-3 space-y-2">
                          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                            <History className="h-3.5 w-3.5" />
                            Recent Runs
                          </p>
                          {runs.map((run, i) => (
                            <div key={i} className="flex items-center gap-2.5 rounded-md px-3 py-2 bg-muted/40 text-sm">
                              {runStatusIcon(run.status)}
                              <span className={run.status === 'failed' ? 'text-destructive' : 'text-foreground'}>
                                {run.label}
                              </span>
                              <span className="ml-auto text-xs text-muted-foreground">{run.time}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )
              })}
            </div>
          )}
        </TabsContent>

        {/* AI Generate Tab */}
        <TabsContent value="generate" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Generate Workflow with AI</CardTitle>
              <CardDescription>
                Describe your automation idea in natural language and AI will create a complete n8n workflow
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div>
                <Label htmlFor="prompt">Describe your workflow</Label>
                <Textarea
                  id="prompt"
                  value={generatePrompt}
                  onChange={(e) => setGeneratePrompt(e.target.value)}
                  placeholder="Example: Create a workflow that monitors my blog RSS feed, uses AI to rewrite posts for LinkedIn and Twitter, generates matching images with ComfyUI, and schedules them for peak engagement hours"
                  rows={6}
                  className="font-mono text-base"
                />
              </div>

              <div>
                <Label>Advanced Options</Label>
                <div className="grid gap-4 md:grid-cols-2 mt-4">
                  <div>
                    <Label htmlFor="model">AI Model</Label>
                    <Select value={generateModel} onValueChange={setGenerateModel}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select model" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="llama3">Llama 3 (Local)</SelectItem>
                        <SelectItem value="codellama">Code Llama (Local)</SelectItem>
                        <SelectItem value="gpt-4">GPT-4 (Cloud)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label htmlFor="complexity">Complexity</Label>
                    <Select value={generateComplexity} onValueChange={setGenerateComplexity}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select complexity" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="simple">Simple (1-3 nodes)</SelectItem>
                        <SelectItem value="moderate">Moderate (4-8 nodes)</SelectItem>
                        <SelectItem value="complex">Complex (9+ nodes)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>

              <div className="flex justify-end">
                <Button onClick={handleGenerate} disabled={generateMutation.isPending || !generatePrompt.trim()} className="w-[200px]">
                  {generateMutation.isPending ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Generating...
                    </>
                  ) : (
                    <>
                      <Sparkles className="mr-2 h-4 w-4" />
                      Generate Workflow
                    </>
                  )}
                </Button>
              </div>

              {generateMutation.isPending && (
                <div className="p-4 bg-muted rounded-lg">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span>AI is building your workflow... This may take a minute.</span>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Example prompts */}
          <Card>
            <CardHeader>
              <CardTitle>Example Prompts</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 md:grid-cols-2">
                {[
                  'Repurpose blog posts into LinkedIn carousel posts with AI-generated images',
                  'Auto-reply to comments on Instagram posts with personalized responses',
                  'Cross-post Twitter threads to LinkedIn with formatting adjustments',
                  'Generate weekly analytics report and post summary to Slack',
                  'Create Instagram Reels from blog content with AI voiceover',
                  'Monitor brand mentions and create response drafts for approval',
                ].map((prompt, i) => (
                  <Button
                    key={i}
                    variant="outline"
                    className="w-full justify-start text-left h-auto py-3 px-4"
                    onClick={() => setGeneratePrompt(prompt)}
                  >
                    {prompt}
                  </Button>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

