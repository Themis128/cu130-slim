import Link from 'next/link'
import {
  Sparkles,
  Share2,
  Calendar,
  BarChart3,
  Palette,
  Search,
  type LucideIcon,
} from 'lucide-react'

interface Feature {
  icon: LucideIcon
  title: string
  description: string
}

const features: Feature[] = [
  {
    icon: Sparkles,
    title: 'AI Content Generation',
    description:
      'Generate on-brand posts, captions, and hashtags with local and Cloudflare AI models — no copywriter required.',
  },
  {
    icon: Share2,
    title: 'Multi-Platform Publishing',
    description:
      'Publish to LinkedIn, Facebook, Instagram, Twitter/X, TikTok, and Threads from a single compose box.',
  },
  {
    icon: Calendar,
    title: 'Smart Scheduling',
    description:
      'Pick optimal posting times with a visual calendar and queue, backed by Celery beat-driven publishing.',
  },
  {
    icon: BarChart3,
    title: 'Analytics & Insights',
    description:
      'Track reach, engagement, and growth across every connected account with unified dashboards.',
  },
  {
    icon: Palette,
    title: 'Brand Management',
    description:
      'Centralize brand kits, color palettes, and tone guidelines so every post stays on brand.',
  },
  {
    icon: Search,
    title: 'SEO Optimization',
    description:
      'Automatic SEO scoring, spellcheck, and plain-English checks on every caption, alt text, and tag.',
  },
]

export function FeatureGrid() {
  return (
    <section className="bg-background py-20">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            Everything you need to scale your social presence
          </h2>
          <p className="mt-4 text-lg text-muted-foreground">
            One platform to plan, create, publish, and measure — powered by AI
            and a Cloudflare-first infrastructure.
          </p>
        </div>

        <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {features.map((feature) => (
            <div
              key={feature.title}
              className="group rounded-xl border border-border bg-card p-6 transition-shadow hover:shadow-md"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <feature.icon className="h-6 w-6" />
              </div>
              <h3 className="mt-4 text-lg font-semibold text-foreground">
                {feature.title}
              </h3>
              <p className="mt-2 text-sm text-muted-foreground">
                {feature.description}
              </p>
            </div>
          ))}
        </div>

        <div className="mt-12 text-center">
          <Link
            href="/features"
            className="text-sm font-medium text-primary hover:underline"
          >
            Explore all features &rarr;
          </Link>
        </div>
      </div>
    </section>
  )
}
