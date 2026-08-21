'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, Zap, Plus, MoreVertical, Play, Trash2, Edit, Copy, Loader2, Sparkles, Brain } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/DropdownMenu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger } from '@/components/ui/Dialog'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { Skeleton } from '@/components/ui/Skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { useTemplates, useCreateTemplate, useGenerateWorkflow, useWorkflows, useDeployWorkflow } from '@/hooks/useQueries'
import type { PromptTemplate, GeneratedWorkflow } from '@/types'
import toast from 'react-hot-toast'

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
  const [generatePrompt, setGeneratePrompt] = useState('')
  const [generateOpen, setGenerateOpen] = useState(false)

  const { data: templatesData, isLoading: templatesLoading } = useTemplates(categoryFilter)
  const { data: workflowsData, isLoading: workflowsLoading } = useWorkflows()
  const createTemplateMutation = useCreateTemplate()
  const generateMutation = useGenerateWorkflow()
  const deployMutation = useDeployWorkflow()

  const templates = templatesData?.items || []
  const workflows = workflowsData?.items || []

  const handleGenerate = async () => {
    if (!generatePrompt.trim()) return
    try {
      await generateMutation.mutateAsync({ prompt: generatePrompt })
      setGeneratePrompt('')
      setGenerateOpen(false)
      setActiveTab('templates')
    } catch {
      toast.error('Failed to generate workflow')
    }
  }

  const handleDeploy = async (templateId: string) => {
    try {
      await deployMutation.mutateAsync(templateId)
    } catch {
      toast.error('Failed to deploy workflow')
    }
  }

  const statusBadge = (status: string) => {
    const variants: Record<string, 'default' | 'success' | 'warning' | 'destructive' | 'secondary'> = {
      draft: 'secondary',
      active: 'success',
      paused: 'warning',
      error: 'destructive',
    }
    return <Badge variant={variants[status] || 'default'}>{status}</Badge>
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
          <Dialog open={generateOpen} onOpenChange={setGenerateOpen}>
            <DialogTrigger asChild>
              <Button>
                <Sparkles className="mr-2 h-4 w-4" />
                AI Generate
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>Generate Workflow with AI</DialogTitle>
                <DialogDescription>
                  Describe what you want to automate and AI will create an n8n workflow for you
                </DialogDescription>
              </DialogHeader>
              <div className="py-4 space-y-4">
                <div>
                  <Label>What should the workflow do?</Label>
                  <Textarea
                    value={generatePrompt}
                    onChange={(e) => setGeneratePrompt(e.target.value)}
                    placeholder="Example: Create a workflow that generates LinkedIn posts from blog RSS feeds, adds AI-generated images, and schedules them for optimal engagement times"
                    rows={4}
                  />
                </div>
                <div>
                  <Label>Target Platforms</Label>
                  <div className="flex flex-wrap gap-2 mt-2">
                    {['linkedin', 'twitter', 'instagram', 'facebook', 'threads'].map((p) => (
                      <Badge key={p} variant="outline" className="capitalize">{p}</Badge>
                    ))}
                  </div>
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setGenerateOpen(false)}>Cancel</Button>
                <Button onClick={handleGenerate} disabled={generateMutation.isPending || !generatePrompt.trim()}>
                  {generateMutation.isPending ? 'Generating...' : 'Generate Workflow'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button asChild>
            <a href="/workflows/new">
              <Plus className="mr-2 h-4 w-4" />
              New Template
            </a>
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
              {[1, 2, 3].map((i) => (
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
          ) : templates.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <Zap className="mx-auto h-12 w-12 text-muted-foreground/50" />
                <h3 className="mt-4 text-lg font-medium">No templates found</h3>
                <p className="mt-2 text-muted-foreground">
                  {search || categoryFilter ? 'Try adjusting your filters' : 'Create your first workflow template'}
                </p>
                {!search && !categoryFilter && (
                  <Button className="mt-4" asChild>
                    <a href="/workflows/new">Create Template</a>
                  </Button>
                )}
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {templates.map((template: PromptTemplate) => (
                <Card key={template.id}>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle className="text-lg">{template.name}</CardTitle>
                        <CardDescription>{template.description}</CardDescription>
                      </div>
                      <Badge variant="outline" className="capitalize">{template.category}</Badge>
                    </div>
                  </CardHeader>
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
                      <span>By {template.user_id || 'Unknown'}</span>
                      <span>{template.usage_count} uses</span>
                    </div>
                  </CardContent>
                  <CardFooter className="flex gap-2">
                    <Button variant="outline" size="sm" className="flex-1" asChild>
                      <a href={`/workflows/${template.id}`}>
                        <Edit className="mr-1.5 h-3.5 w-3.5" />
                        Edit
                      </a>
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => handleDeploy(template.id)} disabled={deployMutation.isPending}>
                      <Play className="mr-1.5 h-3.5 w-3.5" />
                      Deploy
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm">
                          <MoreVertical className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem asChild>
                          <a href={`/workflows/${template.id}`}>
                            <Edit className="mr-2 h-4 w-4" />
                            Edit
                          </a>
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleDeploy(template.id)} disabled={deployMutation.isPending}>
                          <Play className="mr-2 h-4 w-4" />
                          Deploy
                        </DropdownMenuItem>
                        <DropdownMenuItem>
                          <Copy className="mr-2 h-4 w-4" />
                          Duplicate
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem className="text-destructive">
                          <Trash2 className="mr-2 h-4 w-4" />
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </CardFooter>
                </Card>
              ))}
            </div>
          )}
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
              <CardContent className="py-12 text-center">
                <Play className="mx-auto h-12 w-12 text-muted-foreground/50" />
                <h3 className="mt-4 text-lg font-medium">No deployed workflows</h3>
                <p className="mt-2 text-muted-foreground">Deploy a template to start automating</p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-3">
              {workflows.map((workflow: GeneratedWorkflow) => (
                <Card key={workflow.id}>
                  <CardContent className="py-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-4">
                        <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center">
                          <Zap className="h-5 w-5 text-primary" />
                        </div>
                        <div>
                          <p className="font-medium">{workflow.prompt.slice(0, 50)}...</p>
                          <p className="text-sm text-muted-foreground">n8n ID: {workflow.n8n_workflow_id}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-4">
                        {statusBadge(workflow.status)}
                        <Button variant="outline" size="sm" asChild>
                          <a href={`/workflows/${workflow.id}`} target="_blank" rel="noopener noreferrer">
                            <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                            Open in n8n
                          </a>
                        </Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
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
                    <Select>
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
                    <Select>
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

import { ExternalLink } from 'lucide-react'