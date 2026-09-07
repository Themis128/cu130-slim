import type { Metadata } from 'next'
import { PricingTable } from '@/components/marketing/PricingTable'
import { CTASection } from '@/components/marketing/CTASection'

export const metadata: Metadata = {
  title: 'Pricing - SocialAuto',
  description:
    'Simple, transparent pricing for SocialAuto. Start free, upgrade as you grow.',
}

const faqs = [
  {
    q: 'Can I switch plans at any time?',
    a: 'Yes. You can upgrade or downgrade your plan at any time from your account settings. Changes take effect immediately and we prorate the difference.',
  },
  {
    q: 'Do you offer a free trial?',
    a: 'The Free plan is free forever — no credit card required. Paid plans can be cancelled anytime, no long-term contracts.',
  },
  {
    q: 'Which platforms are supported?',
    a: 'SocialAuto supports LinkedIn, Facebook, Instagram, Twitter/X, TikTok, and Threads with secure OAuth connections and automatic token refresh.',
  },
  {
    q: 'What does "unlimited posts" mean?',
    a: 'On the Business and Enterprise plans there is no monthly post cap. Fair-use limits apply to prevent abuse, but they are generous enough for any real-world workflow.',
  },
]

export default function PricingPage() {
  return (
    <>
      <section className="bg-background py-20">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-2xl text-center">
            <h1 className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
              Pricing for every stage
            </h1>
            <p className="mt-4 text-lg text-muted-foreground">
              Start free and scale as you grow. No hidden fees, cancel anytime.
            </p>
          </div>

          <div className="mt-14">
            <PricingTable />
          </div>
        </div>
      </section>

      <section className="bg-muted/30 py-20">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
          <h2 className="text-center text-3xl font-bold tracking-tight text-foreground">
            Frequently asked questions
          </h2>
          <div className="mt-10 space-y-6">
            {faqs.map((faq) => (
              <div
                key={faq.q}
                className="rounded-xl border border-border bg-card p-6"
              >
                <h3 className="text-lg font-semibold text-foreground">
                  {faq.q}
                </h3>
                <p className="mt-2 text-muted-foreground">{faq.a}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <CTASection />
    </>
  )
}
