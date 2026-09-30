'use client'

import { useState } from 'react'

// Newsletter capture for the public funnel — posts to the unauthenticated,
// rate-limited /leads/public endpoint. Hidden `website` field is a honeypot.
export function LeadCapture() {
  const [email, setEmail] = useState('')
  const [website, setWebsite] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')

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
      setState(res.ok ? 'done' : 'error')
    } catch {
      setState('error')
    }
  }

  if (state === 'done') {
    return (
      <p className="text-sm text-green-600 dark:text-green-400">
        Check your inbox — the Cloud Migration Playbook is on its way.
      </p>
    )
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
