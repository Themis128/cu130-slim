'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  Mail,
  Plus,
  Send,
  RefreshCw,
  Loader2,
  Users,
  ShieldBan,
  ClipboardList,
  Database,
  Trash2,
  Wand2,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Textarea } from '@/components/ui/Textarea'
import { Switch } from '@/components/ui/Switch'
import { EmptyState } from '@/components/ui/EmptyState'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import {
  aiApi,
  emailCampaignsApi,
  type EmailCampaign,
  type CampaignRecipient,
  type SurveyResponseRow,
  type SurveyQuestion,
  type CrmClient,
} from '@/services/api'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import { formatErrorToast } from '@/lib/humanizeError'

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  try {
    return format(new Date(iso), 'dd MMM yyyy HH:mm')
  } catch {
    return iso
  }
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'success' | 'warning' | 'destructive'> = {
  draft: 'secondary',
  sending: 'warning',
  sent: 'success',
  partial: 'warning',
  failed: 'destructive',
}

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<EmailCampaign[]>([])
  const [loading, setLoading] = useState(true)
  const [crm, setCrm] = useState<{ contacts: CrmClient[]; leads: CrmClient[] } | null>(null)
  const [crmCounts, setCrmCounts] = useState<Record<string, number> | null>(null)
  const [suppressions, setSuppressions] = useState<{ email: string; reason: string; created_at: string }[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [c, s, cs] = await Promise.all([
        emailCampaignsApi.list(),
        emailCampaignsApi.suppressions().catch(() => ({ data: [] })),
        emailCampaignsApi.crmSummary().catch(() => ({ data: null })),
      ])
      setCampaigns(c.data)
      setSuppressions(s.data as typeof suppressions)
      setCrmCounts(cs.data?.counts ?? null)
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t load campaigns.', err))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadClients = useCallback(async () => {
    try {
      const r = await emailCampaignsApi.crmClients()
      setCrm(r.data)
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t load the CRM client catalog.', err))
    }
  }, [])

  useEffect(() => {
    load()
    loadClients()
  }, [load, loadClients])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Mail className="h-6 w-6" /> Email Campaigns
          </h1>
          <p className="text-sm text-muted-foreground">
            Marketing emails &amp; survey funnels to cloudless.gr clients — audiences from EspoCRM
            {crmCounts ? ` (${crmCounts.lead} leads · ${crmCounts.contact} contacts · ${crmCounts.account} accounts)` : ''}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
          <Button size="sm" onClick={() => setShowCreate((v) => !v)}>
            <Plus className="h-4 w-4 mr-1" /> New campaign
          </Button>
        </div>
      </div>

      {showCreate && (
        <CreateCampaignForm
          onCreated={() => {
            setShowCreate(false)
            load()
          }}
        />
      )}

      <Tabs defaultValue="campaigns">
        <TabsList>
          <TabsTrigger value="campaigns">Campaigns ({campaigns.length})</TabsTrigger>
          <TabsTrigger value="clients">CRM Clients ({(crm?.contacts.length ?? 0) + (crm?.leads.length ?? 0)})</TabsTrigger>
          <TabsTrigger value="suppressions">Suppressions ({suppressions.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="campaigns" className="space-y-3">
          {campaigns.length === 0 && !loading && (
            <EmptyState
              icon={Mail}
              title="No campaigns yet"
              description="Create a marketing email or survey funnel to reach your CRM clients."
            />
          )}
          {campaigns.map((c) => (
            <CampaignCard
              key={c.id}
              campaign={c}
              expanded={expanded === c.id}
              onToggle={() => setExpanded((v) => (v === c.id ? null : c.id))}
              onChanged={load}
            />
          ))}
        </TabsContent>

        <TabsContent value="clients">
          <ClientCatalog crm={crm} onAdded={load} />
        </TabsContent>

        <TabsContent value="suppressions">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldBan className="h-5 w-5" /> Do-not-contact list
              </CardTitle>
              <CardDescription>
                Suppressed addresses are skipped on every send. Added via unsubscribe links or manually.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {suppressions.length === 0 ? (
                <p className="text-sm text-muted-foreground">No suppressed addresses.</p>
              ) : (
                <ul className="text-sm space-y-1">
                  {suppressions.map((s) => (
                    <li key={s.email} className="flex justify-between">
                      <span>{s.email}</span>
                      <span className="text-muted-foreground">{s.reason} · {fmtDate(s.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function CreateCampaignForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'marketing' | 'survey'>('marketing')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [surveyUrl, setSurveyUrl] = useState('')
  const [questionsJson, setQuestionsJson] = useState('')
  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState(false)
  const [drafting, setDrafting] = useState(false)

  // Draft email copy via /api/v1/ai/generate-content with platform="email" —
  // the Visibility Era voice signature (subject-as-hook, one point, one CTA)
  // is injected server-side. Contract: content starts with "SUBJECT: ...".
  const draftWithAi = async () => {
    if (!brief.trim()) {
      toast.error('Describe the email first — who it is for and what it should achieve')
      return
    }
    setDrafting(true)
    try {
      const res = await aiApi.generateContent({
        prompt: brief.trim(),
        platform: 'email',
        tone: 'professional',
        length: 'medium',
        include_hashtags: false,
        include_emojis: false,
      })
      const data =
        (res as { data?: { content?: string; subject?: string; warnings?: string[] } })?.data ?? {}
      const raw = data.content ?? ''
      // EMAIL_SCHEMA returns a dedicated subject; tolerate an inline
      // "SUBJECT: ..." first line if an older/model fallback produced one.
      const m = raw.match(/^\s*SUBJECT:\s*(.+?)\n+([\s\S]*)$/i)
      if (data.subject) {
        setSubject(data.subject.trim())
        setBody(raw.trim())
      } else if (m) {
        setSubject(m[1].trim())
        setBody(m[2].trim())
      } else {
        setBody(raw.trim())
      }
      toast.success('Draft ready — review before creating')
      if (data.warnings?.length) {
        toast(data.warnings.join(' '), { icon: '⚠️', duration: 8000 })
      }
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      toast.error(
        status === 524 || status === 504
          ? 'The AI model is still warming up — try again in a minute.'
          : formatErrorToast('AI draft failed.', err),
      )
    } finally {
      setDrafting(false)
    }
  }

  const submit = async () => {
    if (!name.trim() || !subject.trim() || !body.trim()) {
      toast.error('Name, subject and body are required')
      return
    }
    let questions: SurveyQuestion[] = []
    if (kind === 'survey' && questionsJson.trim()) {
      try {
        questions = JSON.parse(questionsJson)
        if (!Array.isArray(questions)) throw new Error('not an array')
      } catch {
        toast.error('Questions must be a JSON array, e.g. [{"id":"q1","label":"…","type":"rating","required":true}]')
        return
      }
    }
    setBusy(true)
    try {
      await emailCampaignsApi.create({
        name: name.trim(),
        kind,
        subject: subject.trim(),
        text_body: body,
        survey_url: surveyUrl.trim() || null,
        questions,
      })
      toast.success('Campaign created — add an audience next')
      onCreated()
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t create the campaign.', err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New campaign</CardTitle>
        <CardDescription>
          Merge tags: {'{{first_name}}'}, {'{{name}}'}, {'{{email}}'}, {'{{survey_url}}'}, {'{{unsubscribe_url}}'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="October newsletter" />
          </div>
          <div>
            <Label>Kind</Label>
            <select
              className="w-full h-10 rounded-md border bg-background px-3 text-sm"
              value={kind}
              onChange={(e) => setKind(e.target.value as 'marketing' | 'survey')}
            >
              <option value="marketing">Marketing email</option>
              <option value="survey">Survey funnel</option>
            </select>
          </div>
        </div>
        <div className="rounded-md border border-dashed p-3 space-y-2">
          <Label>Draft with AI (Sofia Kakkava voice)</Label>
          <div className="flex gap-2">
            <Input
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder='e.g. "Service catalog for small-business owners — audit CTA" or "follow-up: audit no-shows"'
            />
            <Button type="button" variant="secondary" onClick={draftWithAi} disabled={drafting}>
              {drafting ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Wand2 className="h-4 w-4 mr-1" />}
              Draft
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Subject-as-hook, one point fully explained, one CTA — the Visibility Era formula.
            Use merge tags like {'{{first_name}}'} in the brief if wanted.
          </p>
        </div>
        <div>
          <Label>Subject</Label>
          <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </div>
        <div>
          <Label>Body (plain text)</Label>
          <Textarea rows={5} value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        {kind === 'survey' && (
          <>
            <div>
              <Label>Survey URL (optional — leave empty for the hosted per-recipient form)</Label>
              <Input value={surveyUrl} onChange={(e) => setSurveyUrl(e.target.value)} placeholder="https://…" />
            </div>
            <div>
              <Label>Questions (JSON array)</Label>
              <Textarea
                rows={4}
                value={questionsJson}
                onChange={(e) => setQuestionsJson(e.target.value)}
                placeholder='[{"id":"sat","label":"Rate 1-5","type":"rating","required":true},{"id":"notes","label":"Comments","type":"text"}]'
              />
            </div>
          </>
        )}
        <Button onClick={submit} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Plus className="h-4 w-4 mr-1" />}
          Create
        </Button>
      </CardContent>
    </Card>
  )
}

function CampaignCard({
  campaign: c,
  expanded,
  onToggle,
  onChanged,
}: {
  campaign: EmailCampaign
  expanded: boolean
  onToggle: () => void
  onChanged: () => void
}) {
  const [recipients, setRecipients] = useState<CampaignRecipient[] | null>(null)
  const [responses, setResponses] = useState<SurveyResponseRow[] | null>(null)
  const [emailsText, setEmailsText] = useState('')
  const [inclLeads, setInclLeads] = useState(false)
  const [inclCrm, setInclCrm] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const loadDetail = useCallback(async () => {
    const [r, resp] = await Promise.all([
      emailCampaignsApi.recipients(c.id).catch(() => ({ data: [] })),
      c.kind === 'survey'
        ? emailCampaignsApi.responses(c.id).catch(() => ({ data: [] }))
        : Promise.resolve({ data: [] }),
    ])
    setRecipients(r.data as CampaignRecipient[])
    setResponses(resp.data as SurveyResponseRow[])
  }, [c.id, c.kind])

  useEffect(() => {
    if (expanded && recipients === null) loadDetail()
  }, [expanded, recipients, loadDetail])

  const addAudience = async () => {
    const emails = emailsText
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((email) => ({ email }))
    if (!emails.length && !inclLeads && !inclCrm) {
      toast.error('Add emails or enable a source')
      return
    }
    setBusy('audience')
    try {
      const r = await emailCampaignsApi.addAudience(c.id, {
        emails,
        include_socialauto_leads: inclLeads,
        include_espocrm: inclCrm,
      })
      toast.success(`Queued ${r.data.queued} · suppressed ${r.data.skipped_suppressed}`)
      setEmailsText('')
      setRecipients(null)
      loadDetail()
      onChanged()
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t add the audience.', err))
    } finally {
      setBusy(null)
    }
  }

  const send = async () => {
    setBusy('send')
    try {
      const r = await emailCampaignsApi.send(c.id)
      toast.success(`Sending ${r.data.pending} recipient(s)`)
      setTimeout(loadDetail, 5000)
      onChanged()
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t send.', err))
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    setBusy('delete')
    try {
      await emailCampaignsApi.delete(c.id)
      toast.success('Draft campaign deleted')
      onChanged()
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t delete.', err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={onToggle}>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              {c.kind === 'survey' ? <ClipboardList className="h-4 w-4" /> : <Mail className="h-4 w-4" />}
              {c.name}
            </CardTitle>
            <CardDescription>
              {c.subject} · created {fmtDate(c.created_at)}
              {c.sent_at ? ` · sent ${fmtDate(c.sent_at)}` : ''}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {c.stats &&
              Object.entries(c.stats).map(([k, v]) => (
                <Badge key={k} variant="secondary">
                  {k}: {v}
                </Badge>
              ))}
            <Badge variant={STATUS_VARIANT[c.status] ?? 'secondary'}>{c.status}</Badge>
          </div>
        </div>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-4 border-t pt-4">
          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              <Users className="h-4 w-4" /> Add audience
            </Label>
            <Textarea
              rows={2}
              value={emailsText}
              onChange={(e) => setEmailsText(e.target.value)}
              placeholder="client@example.com, another@example.com — one per line or comma-separated"
            />
            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={inclLeads} onCheckedChange={setInclLeads} /> SocialAuto leads
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={inclCrm} onCheckedChange={setInclCrm} /> EspoCRM clients
              </label>
              <Button size="sm" variant="outline" onClick={addAudience} disabled={busy === 'audience'}>
                {busy === 'audience' ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Add'}
              </Button>
              <Button size="sm" onClick={send} disabled={busy === 'send' || c.status === 'sent'}>
                {busy === 'send' ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Send className="h-4 w-4 mr-1" />}
                Send now
              </Button>
              {c.status === 'draft' && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={remove}
                  disabled={busy === 'delete'}
                  title="Delete draft"
                >
                  {busy === 'delete' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                </Button>
              )}
            </div>
          </div>

          {recipients && recipients.length > 0 && (
            <div>
              <Label>Recipients</Label>
              <ul className="text-sm space-y-1 mt-1 max-h-48 overflow-y-auto">
                {recipients.map((r) => (
                  <li key={r.email} className="flex justify-between">
                    <span>
                      {r.email} {r.name ? `(${r.name})` : ''}
                    </span>
                    <span className="text-muted-foreground">
                      {r.status}
                      {r.sent_at ? ` · ${fmtDate(r.sent_at)}` : ''}
                      {r.error ? ` · ${r.error.slice(0, 80)}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {responses !== null && (
            <div>
              <Label>Survey responses ({responses.length})</Label>
              {responses.length === 0 ? (
                <p className="text-sm text-muted-foreground">No responses yet.</p>
              ) : (
                <ul className="text-sm space-y-2 mt-1">
                  {responses.map((r, i) => (
                    <li key={i} className="border rounded p-2">
                      <div className="flex justify-between text-muted-foreground text-xs mb-1">
                        <span>{r.email ?? 'anonymous'} {r.espocrm_noted ? '· CRM ✓' : ''}</span>
                        <span>{fmtDate(r.submitted_at)}</span>
                      </div>
                      {Object.entries(r.answers).map(([k, v]) => (
                        <div key={k}>
                          <b>{k}:</b> {String(v)}
                        </div>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function ClientCatalog({
  crm,
  onAdded,
}: {
  crm: { contacts: CrmClient[]; leads: CrmClient[] } | null
  onAdded: () => void
}) {
  if (crm === null) {
    return (
      <Card>
        <CardContent className="py-8 flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading EspoCRM client catalog…
        </CardContent>
      </Card>
    )
  }
  const all = [
    ...crm.contacts.map((c) => ({ ...c, _type: 'Contact' })),
    ...crm.leads.map((l) => ({ ...l, _type: 'Lead' })),
  ]
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="h-5 w-5" /> EspoCRM client catalog
        </CardTitle>
        <CardDescription>
          {all.length} client(s) — enable “EspoCRM clients” in a campaign’s audience to include them all.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {all.length === 0 ? (
          <p className="text-sm text-muted-foreground">No CRM clients with an email address yet.</p>
        ) : (
          <ul className="text-sm space-y-1">
            {all.map((cl) => (
              <li key={`${cl._type}-${cl.id}`} className="flex justify-between">
                <span>
                  {cl.name} {cl.email ? `· ${cl.email}` : ''}
                </span>
                <span className="text-muted-foreground">
                  {cl._type}
                  {cl.status ? ` · ${cl.status}` : ''}
                  {cl.account ? ` · ${cl.account}` : ''}
                  {cl.source ? ` · ${cl.source}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
