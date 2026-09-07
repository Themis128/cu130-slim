import Link from 'next/link'
import { Check } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'

interface PricingTier {
  name: string
  price: string
  period?: string
  description: string
  features: string[]
  cta: string
  highlighted?: boolean
}

const tiers: PricingTier[] = [
  {
    name: 'Free',
    price: '€0',
    period: '/mo',
    description: 'For individuals getting started with automation.',
    features: [
      '1 social account',
      '10 posts per month',
      'Basic AI content generation',
      'Community support',
    ],
    cta: 'Get Started',
  },
  {
    name: 'Pro',
    price: '€29',
    period: '/mo',
    description: 'For creators and small teams scaling output.',
    features: [
      '5 social accounts',
      '100 posts per month',
      'Advanced AI content generation',
      'Analytics & insights',
      'Email support',
    ],
    cta: 'Get Started',
    highlighted: true,
  },
  {
    name: 'Business',
    price: '€99',
    period: '/mo',
    description: 'For growing teams that need collaboration.',
    features: [
      '20 social accounts',
      'Unlimited posts',
      'Team members',
      'Brand kit management',
      'Priority support',
    ],
    cta: 'Get Started',
  },
  {
    name: 'Enterprise',
    price: 'Custom',
    description: 'For organizations with advanced requirements.',
    features: [
      'Unlimited everything',
      'Single sign-on (SSO)',
      'Dedicated support',
      'Custom integrations',
    ],
    cta: 'Contact Sales',
  },
]

export function PricingTable() {
  return (
    <div className="grid gap-6 lg:grid-cols-4">
      {tiers.map((tier) => (
        <div
          key={tier.name}
          className={cn(
            'relative flex flex-col rounded-xl border bg-card p-6',
            tier.highlighted
              ? 'border-primary shadow-lg ring-1 ring-primary'
              : 'border-border'
          )}
        >
          {tier.highlighted && (
            <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground">
              Most Popular
            </span>
          )}
          <h3 className="text-lg font-semibold text-foreground">{tier.name}</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {tier.description}
          </p>
          <div className="mt-4 flex items-baseline gap-1">
            <span className="text-4xl font-bold text-foreground">
              {tier.price}
            </span>
            {tier.period && (
              <span className="text-sm text-muted-foreground">
                {tier.period}
              </span>
            )}
          </div>

          <ul className="mt-6 flex-1 space-y-3">
            {tier.features.map((feature) => (
              <li key={feature} className="flex items-start gap-2 text-sm">
                <Check className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                <span className="text-muted-foreground">{feature}</span>
              </li>
            ))}
          </ul>

          <Button
            asChild
            className="mt-8"
            variant={tier.highlighted ? 'default' : 'outline'}
          >
            <Link href="/register">{tier.cta}</Link>
          </Button>
        </div>
      ))}
    </div>
  )
}
