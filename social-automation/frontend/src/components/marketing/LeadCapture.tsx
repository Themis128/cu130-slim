'use client'

import { useState } from 'react'

type Delivery = 'email' | 'already_sent' | 'download' | 'none'
type LeadResult = { playbook_delivery?: Delivery; playbook_url?: string | null }

// Newsletter capture for the public funnel — posts to the unauthenticated,
// rate-limited /leads/public endpoint. Hidden `website` field is a honeypot.
export function LeadCapture() {
  const [email, setEmail] = useState('')
  const [website, setWebsite] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')
  const [result, setResult] = useState<LeadResult>({})

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!email || state === 'sending') return
    setState('sending')
    try {
      const res = await fetch('/api/v1/leads/public', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, website }),
      })
      if (!res.ok) {
        setState('error')
        return
      }
      setResult(((await res.json().catch(() => ({}))) as LeadResult) || {})
      setState('done')
    } catch {
      setState('error')
    }
  }

  if (state === 'done') {
    return <LeadCaptureDone result={result} />
  }

  return (
    <form onSubmit={submit} className="flex w-full flex-col gap-2 sm:flex-row">
      <input
        type="text"
        value={website}
        onChange={(e) => setWebsite(e.target.value)}
        className="hidden"
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
      />
      <input
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@company.com"
        className="w-full flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
      />
      <button
        type="submit"
        disabled={state === 'sending'}
        className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background hover:bg-foreground/90 disabled:opacity-50"
      >
        {state === 'sending' ? 'Sending…' : 'Get the Playbook'}
      </button>
      {state === 'error' && (
        <p className="text-xs text-red-500">Something went wrong — try again.</p>
      )}
    </form>
  )
}

// Success copy must match what the backend actually did: only promise an
// email when one was queued; always offer the direct download when we have it.
export function LeadCaptureDone({ result }: { result: LeadResult }) {
  const url = result.playbook_url || null
  const delivery: Delivery = result.playbook_delivery ?? (url ? 'download' : 'none')
  const message =
    delivery === 'email'
      ? 'Check your inbox — the Cloud Migration Playbook is on its way.'
      : delivery === 'already_sent'
        ? "You're already on the list — we sent you the playbook earlier."
        : delivery === 'download'
          ? "Thanks — you're on the list."
          : "Thanks — you're on the list. We'll be in touch."
  return (
    <div className="text-sm text-green-600 dark:text-green-400">
      <p>{message}</p>
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener"
          className="mt-2 inline-block font-semibold text-primary underline"
        >
          {delivery === 'email' ? 'Or download it now (PDF)' : 'Download the playbook (PDF)'}
        </a>
      )}
    </div>
  )
}
