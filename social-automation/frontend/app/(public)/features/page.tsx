import type { Metadata } from 'next'
import Link from 'next/link'
import {
  Sparkles,
  Share2,
  Calendar,
  BarChart3,
  Palette,
  Search,
  ArrowRight,
  type LucideIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { CTASection } from '@/components/marketing/CTASection'

export const metadata: Metadata = {
  title: 'Features - SocialAuto',
  description:
    'Deep dive into SocialAuto features: AI content generation, multi-platform publishing, smart scheduling, analytics, brand management, and SEO optimization.',
}

interface FeatureDetail {
  icon: LucideIcon
  title: string
  description: string
  bullets: string[]
  mockup: string
}

const details: FeatureDetail[] = [
  {
    icon: Sparkles,
    title: 'AI Content Generation',
    description:
      'Generate on-brand posts, captions, and hashtags using a local-first AI fallback chain (Docker Model Runner → Cloudflare Workers AI). Every caption is spell-checked and SEO-scored before it reaches the composer.',
    bullets: [
      'Local + cloud AI fallback chain',
      'Tone and brand-kit aware prompts',
      'Automatic spellcheck and plain-English rewrite',
    ],
    mockup: 'AI composer suggesting captions and hashtags in real time',
  },
  {
    icon: Share2,
    title: 'Multi-Platform Publishing',
    description:
      'Publish to LinkedIn, Facebook, Instagram, Twitter/X, TikTok, and Threads from a single compose box. OAuth tokens are refreshed automatically by a Celery beat task.',
    bullets: [
      'Six platforms, one composer',
      'Automatic token refresh every hour',
      'Per-platform media adaptation',
    ],
    mockup: 'Composer with platform toggles and a live preview per network',
  },
  {
    icon: Calendar,
    title: 'Smart Scheduling',
    description:
      'Plan your content with a visual calendar and queue. A Celery beat scheduler publishes at the exact time you choose, with timezone-aware Europe/Athens defaults.',
    bullets: [
      'Drag-and-drop calendar',
      'Queue with optimal-time suggestions',
      'Timezone-aware publishing',
    ],
    mockup: 'Monthly calendar grid with scheduled posts colour-coded by platform',
  },
  {
    icon: BarChart3,
    title: 'Analytics & Insights',
    description:
      'Track reach, engagement, and growth across every connected account with unified dashboards. Export-ready reports keep your team aligned.',
    bullets: [
      'Unified cross-platform metrics',
      'Growth and engagement trends',
      'Export-ready reports',
    ],
    mockup: 'Analytics dashboard with reach, engagement, and follower charts',
  },
  {
    icon: Palette,
    title: 'Brand Management',
    description:
      'Centralize brand kits, color palettes, and tone guidelines. Every generated post stays on brand, with compliance checks before publishing.',
    bullets: [
      'Brand kits and color palettes',
      'Tone and voice guidelines',
      'Pre-publish compliance checks',
    ],
    mockup: 'Brand kit panel with logo, palette, and tone sliders',
  },
  {
    icon: Search,
    title: 'SEO Optimization',
    description:
      'Automatic SEO scoring, spellcheck, and plain-English checks run on every caption, alt text, and tag — so your content is discoverable and accessible.',
    bullets: [
      'SEO score per post',
      'Spellcheck and grammar correction',
      'Accessible alt text and tags',
    ],
    mockup: 'SEO scorecard with checklist for a scheduled post',
  },
]

export default function FeaturesPage() {
  return (
    <>
      <section className="bg-background py-20">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-2xl text-center">
            <h1 className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
              Features that power your workflow
            </h1>
            <p className="mt-4 text-lg text-muted-foreground">
              From AI content generation to analytics, SocialAuto covers the
              entire social media lifecycle.
            </p>
          </div>

          <div className="mt-16 space-y-16">
            {details.map((feature, idx) => (
              <div
                key={feature.title}
                className={`grid items-center gap-10 lg:grid-cols-2 ${
                  idx % 2 === 1 ? 'lg:[&>*:first-child]:order-2' : ''
                }`}
              >
                <div>
                  <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <feature.icon className="h-6 w-6" />
                  </div>
                  <h2 className="mt-4 text-2xl font-bold text-foreground">
                    {feature.title}
                  </h2>
                  <p className="mt-3 text-muted-foreground">
                    {feature.description}
                  </p>
                  <ul className="mt-4 space-y-2">
                    {feature.bullets.map((b) => (
                      <li
                        key={b}
                        className="flex items-start gap-2 text-sm text-muted-foreground"
                      >
                        <span className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-primary" />
                        {b}
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Placeholder mockup */}
                <div className="flex aspect-video items-center justify-center rounded-xl border border-border bg-gradient-to-br from-muted to-card p-6 text-center">
                  <p className="text-sm text-muted-foreground">
                    {feature.mockup}
                  </p>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-16 text-center">
            <Button asChild size="lg">
              <Link href="/register">
                Try it free
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </div>
        </div>
      </section>

      <CTASection />
    </>
  )
}
