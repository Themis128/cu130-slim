'use client'

import { useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import Script from 'next/script'
import { Check, CreditCard, ExternalLink, Loader2, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Separator } from '@/components/ui/Separator'
import { billingApi } from '@/services/api'
import toast from 'react-hot-toast'
import { formatErrorToast } from '@/lib/humanizeError'

declare global {
  interface Window {
    Paddle?: any
  }
}

interface BillingConfig {
  configured: boolean
  environment: string
  team_id: string
  customer_email: string
  client_token: string | null
  prices: Record<string, string | null>
}

interface Plan {
  tier: string
  limits: { posts_per_month: number; ai_calls_per_month: number; social_accounts: number }
  purchasable: boolean
  price_id: string | null
}

interface Subscription {
  plan_tier: string
  subscription_status: string
  subscription_period_end: string | null
  paddle_customer_id: string | null
  paddle_subscription_id: string | null
}

const TIER_LABELS: Record<string, string> = {
  free: 'Free',
  pro: 'Pro',
  business: 'Business',
  enterprise: 'Enterprise',
}

const STATUS_LABELS: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' }> = {
  none: { label: 'No subscription', variant: 'secondary' },
  active: { label: 'Active', variant: 'default' },
  trialing: { label: 'Trial', variant: 'secondary' },
  past_due: { label: 'Past due', variant: 'destructive' },
  paused: { label: 'Paused', variant: 'outline' },
  canceled: { label: 'Canceled', variant: 'outline' },
  canceled_pending: { label: 'Cancels at period end', variant: 'outline' },
  expired: { label: 'Expired', variant: 'destructive' },
}

function limitText(v: number): string {
  return v === -1 ? 'Unlimited' : v.toLocaleString()
}

export default function BillingPage() {
  const searchParams = useSearchParams()
  const [config, setConfig] = useState<BillingConfig | null>(null)
  const [plans, setPlans] = useState<Plan[]>([])
  const [sub, setSub] = useState<Subscription | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyTier, setBusyTier] = useState<string | null>(null)
  const [portalLoading, setPortalLoading] = useState(false)
  const [paddleReady, setPaddleReady] = useState(false)

  const load = async () => {
    try {
      const [cfg, pl, s] = await Promise.all([
        billingApi.config(),
        billingApi.plans(),
        billingApi.subscription(),
      ])
      setConfig(cfg.data)
      setPlans(pl.data.plans)
      setSub(s.data)
    } catch (e) {
      toast.error(formatErrorToast('Failed to load billing', e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // Reconcile after checkout redirect
  useEffect(() => {
    if (searchParams.get('checkout') === 'success') {
      billingApi.sync().then(() => {
        toast.success('Subscription updated')
        load()
      }).catch(() => {})
    }
  }, [searchParams])

  const openCheckout = (tier: string) => {
    if (!config) return
    const priceId = config.prices[tier]
    if (paddleReady && window.Paddle && priceId) {
      window.Paddle.Checkout.open({
        items: [{ priceId, quantity: 1 }],
        customData: { team_id: config.team_id },
        customer: { email: config.customer_email },
        settings: {
          successUrl: `${window.location.origin}/settings/billing?checkout=success`,
        },
      })
      return
    }
    // Fallback: server-created hosted checkout
    setBusyTier(tier)
    billingApi.checkout(tier)
      .then((res) => {
        if (res.data.checkout_url) window.location.href = res.data.checkout_url
        else toast.error('Checkout URL unavailable — use Paddle.js overlay')
      })
      .catch((e) => toast.error(formatErrorToast('Checkout failed', e)))
      .finally(() => setBusyTier(null))
  }

  const openPortal = async () => {
    setPortalLoading(true)
    try {
      const res = await billingApi.portal()
      if (res.data.portal_url) window.open(res.data.portal_url, '_blank')
    } catch (e) {
      toast.error(formatErrorToast('Could not open billing portal', e))
    } finally {
      setPortalLoading(false)
    }
  }

  const cancel = async () => {
    if (!confirm('Cancel your subscription? Your plan stays active until the end of the billing period.')) return
    try {
      await billingApi.cancel()
      toast.success('Subscription will cancel at period end')
      load()
    } catch (e) {
      toast.error(formatErrorToast('Cancel failed', e))
    }
  }

  if (loading) {
    return <div className="flex justify-center p-12"><Loader2 className="h-8 w-8 animate-spin" /></div>
  }

  const status = STATUS_LABELS[sub?.subscription_status || 'none'] || STATUS_LABELS.none
  const currentTier = sub?.plan_tier || 'free'

  return (
    <div className="space-y-6 p-6 max-w-5xl">
      {config?.configured && config.client_token && (
        <Script
          src="https://cdn.paddle.com/paddle/v2/paddle.js"
          onLoad={() => {
            setTimeout(() => {
              try {
                if (window.Paddle) {
                  if (config.environment !== 'production') {
                    window.Paddle.Environment.set('sandbox')
                  }
                  window.Paddle.Initialize({
                    token: config.client_token,
                    pwCustomer: sub?.paddle_customer_id ? { id: sub.paddle_customer_id } : {},
                  })
                  setPaddleReady(true)
                }
              } catch (err) {
                console.error('Paddle.js init failed:', err)
              }
            }, 100)
          }}
        />
      )}

      <div>
        <h1 className="text-2xl font-bold">Billing &amp; Plan</h1>
        <p className="text-muted-foreground">Manage your subscription and plan limits.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Current plan</CardTitle>
          <CardDescription>Your team's subscription state</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <span className="text-3xl font-bold">{TIER_LABELS[currentTier] || currentTier}</span>
            <Badge variant={status.variant}>{status.label}</Badge>
          </div>
          {sub?.subscription_period_end && (
            <p className="text-sm text-muted-foreground">
              Current period ends {new Date(sub.subscription_period_end).toLocaleDateString()}
            </p>
          )}
          {!config?.configured && (
            <p className="text-sm text-amber-600">
              Billing is not configured yet — set PADDLE_API_KEY and PADDLE_CLIENT_TOKEN to enable checkout.
            </p>
          )}
          <div className="flex gap-3">
            {sub?.paddle_customer_id && (
              <Button variant="outline" onClick={openPortal} disabled={portalLoading}>
                {portalLoading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <CreditCard className="h-4 w-4 mr-2" />}
                Manage billing
                <ExternalLink className="h-3 w-3 ml-1" />
              </Button>
            )}
            {sub?.paddle_subscription_id && sub?.subscription_status === 'active' && (
              <Button variant="outline" onClick={cancel}>
                <XCircle className="h-4 w-4 mr-2" />
                Cancel subscription
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Separator />

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {plans.map((plan) => {
          const isCurrent = plan.tier === currentTier
          return (
            <Card key={plan.tier} className={isCurrent ? 'border-primary' : ''}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  {TIER_LABELS[plan.tier] || plan.tier}
                  {isCurrent && <Badge>Current</Badge>}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <ul className="text-sm space-y-1.5">
                  <li className="flex items-center gap-2">
                    <Check className="h-3.5 w-3.5 text-green-500" />
                    {limitText(plan.limits.posts_per_month)} posts/mo
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="h-3.5 w-3.5 text-green-500" />
                    {limitText(plan.limits.ai_calls_per_month)} AI calls/mo
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="h-3.5 w-3.5 text-green-500" />
                    {limitText(plan.limits.social_accounts)} social accounts
                  </li>
                </ul>
                {plan.tier !== 'free' && !isCurrent && (
                  <Button
                    className="w-full"
                    disabled={!plan.purchasable || busyTier === plan.tier || !config?.configured}
                    onClick={() => openCheckout(plan.tier)}
                  >
                    {busyTier === plan.tier && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    {plan.purchasable ? `Upgrade to ${TIER_LABELS[plan.tier]}` : 'Not available'}
                  </Button>
                )}
                {isCurrent && plan.tier !== 'free' && (
                  <p className="text-xs text-muted-foreground text-center">Your current plan</p>
                )}
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
