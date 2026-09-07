import type { Metadata } from 'next'
import Link from 'next/link'
import { Mail, Globe, MapPin } from 'lucide-react'
import { CTASection } from '@/components/marketing/CTASection'

export const metadata: Metadata = {
  title: 'About - SocialAuto',
  description:
    'SocialAuto is built by Cloudless IT Solutions — a Cloudflare-first, AI-powered social media automation platform.',
}

export default function AboutPage() {
  return (
    <>
      <section className="bg-background py-20">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
          <h1 className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            About SocialAuto
          </h1>
          <p className="mt-6 text-lg text-muted-foreground">
            SocialAuto is built by{' '}
            <span className="font-semibold text-foreground">
              Cloudless IT Solutions
            </span>
            , a team obsessed with making powerful automation accessible to
            everyone. We combine local AI inference with a Cloudflare-first
            infrastructure to deliver fast, private, and cost-effective social
            media automation.
          </p>

          <div className="mt-12 rounded-xl border border-border bg-card p-8">
            <h2 className="text-2xl font-bold text-foreground">Our mission</h2>
            <p className="mt-3 text-muted-foreground">
              To give every creator, brand, and team the tools to plan, create,
              publish, and measure their social media — without the busywork.
              We believe automation should be transparent, privacy-respecting,
              and powered by open, composable infrastructure.
            </p>
          </div>

          <div className="mt-12">
            <h2 className="text-2xl font-bold text-foreground">
              Get in touch
            </h2>
            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              <a
                href="mailto:hello@cloudless.gr"
                className="flex items-center gap-3 rounded-xl border border-border bg-card p-5 transition-colors hover:border-primary"
              >
                <Mail className="h-5 w-5 text-primary" />
                <div>
                  <p className="text-sm font-medium text-foreground">Email</p>
                  <p className="text-sm text-muted-foreground">
                    hello@cloudless.gr
                  </p>
                </div>
              </a>
              <a
                href="https://cloudless.gr"
                className="flex items-center gap-3 rounded-xl border border-border bg-card p-5 transition-colors hover:border-primary"
              >
                <Globe className="h-5 w-5 text-primary" />
                <div>
                  <p className="text-sm font-medium text-foreground">Website</p>
                  <p className="text-sm text-muted-foreground">
                    cloudless.gr
                  </p>
                </div>
              </a>
              <div className="flex items-center gap-3 rounded-xl border border-border bg-card p-5 sm:col-span-2">
                <MapPin className="h-5 w-5 text-primary" />
                <div>
                  <p className="text-sm font-medium text-foreground">
                    Location
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Athens, Greece
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-12 text-center">
            <Link
              href="/register"
              className="text-sm font-medium text-primary hover:underline"
            >
              Start using SocialAuto &rarr;
            </Link>
          </div>
        </div>
      </section>

      <CTASection />
    </>
  )
}
