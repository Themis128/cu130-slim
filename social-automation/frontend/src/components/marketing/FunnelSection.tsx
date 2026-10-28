'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowRight, BookOpen, Zap, Package } from 'lucide-react'
import { LeadCapture } from './LeadCapture'

const UTM = '?utm_source=social&utm_medium=referral&utm_campaign=build-in-public'

type Post = {
  slug: string
  title: string
  excerpt: string
  date: string
  readTime: string
}

// Hybrid monetization funnel: high-ticket audit CTA, low-ticket store
// product, newsletter capture, and a live authority card from the
// cloudless.gr blog.
export function FunnelSection() {
  const [post, setPost] = useState<Post | null>(null)

  useEffect(() => {
    fetch('/api/latest-post')
      .then((r) => r.json())
      .then((d) => setPost(d.post))
      .catch(() => {})
  }, [])

  return (
    <section className="border-y border-border bg-muted/30 py-16">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="grid gap-8 lg:grid-cols-3">
          {/* High-ticket hook — free audit for teams in a cost emergency */}
          <a
            href={`https://cloudless.gr/contact${UTM}`}
            className="group flex flex-col justify-between rounded-2xl border border-border bg-background p-6 transition-colors hover:border-primary"
          >
            <div>
              <Zap className="h-6 w-6 text-primary" />
              <h3 className="mt-4 text-xl font-bold">
                Cloud bill out of control?
              </h3>
              <p className="mt-2 text-sm text-muted-foreground">
                Overpaying on AWS or fighting downtime? Get a concrete fix
                plan — not a sales pitch.
              </p>
            </div>
            <span className="mt-6 inline-flex items-center font-semibold text-primary">
              Book a Free 30-Minute Audit
              <ArrowRight className="ml-2 h-4 w-4 transition-transform group-hover:translate-x-1" />
            </span>
          </a>

          {/* Low-ticket — serverless course for DIY builders */}
          <a
            href={`https://cloudless.gr/en/store/dig-serverless-course${UTM}`}
            className="group flex flex-col justify-between rounded-2xl border border-border bg-background p-6 transition-colors hover:border-primary"
          >
            <div>
              <Package className="h-6 w-6 text-primary" />
              <h3 className="mt-4 text-xl font-bold">
                Build it yourself instead
              </h3>
              <p className="mt-2 text-sm text-muted-foreground">
                The exact Workers + D1 + R2 framework we run in production —
                source code included.
              </p>
            </div>
            <span className="mt-6 inline-flex items-center font-semibold text-primary">
              Get the Serverless Masterclass
              <ArrowRight className="ml-2 h-4 w-4 transition-transform group-hover:translate-x-1" />
            </span>
          </a>

          {/* Lead capture — newsletter for the not-buying-today majority */}
          <div className="flex flex-col justify-between rounded-2xl border border-border bg-background p-6">
            <div>
              <BookOpen className="h-6 w-6 text-primary" />
              <h3 className="mt-4 text-xl font-bold">
                Free Cloud Migration Playbook
              </h3>
              <p className="mt-2 text-sm text-muted-foreground">
                Weekly cloud and growth tactics. No spam, unsubscribe anytime.
              </p>
            </div>
            <div className="mt-6">
              <LeadCapture />
            </div>
          </div>
        </div>

        {/* Authority booster — latest cloudless.gr article, fetched live */}
        {post && (
          <Link
            href={`https://cloudless.gr/en/blog/${post.slug}${UTM}`}
            className="group mt-8 flex items-start gap-4 rounded-2xl border border-border bg-background p-5 transition-colors hover:border-primary"
          >
            <div className="min-w-0 flex-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Latest from the blog · {post.readTime}
              </span>
              <p className="mt-1 font-semibold leading-snug">{post.title}</p>
              <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                {post.excerpt}
              </p>
            </div>
            <ArrowRight className="mt-1 h-5 w-5 shrink-0 text-primary transition-transform group-hover:translate-x-1" />
          </Link>
        )}
      </div>
    </section>
  )
}
