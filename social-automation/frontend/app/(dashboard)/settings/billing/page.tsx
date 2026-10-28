'use client'

import { useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import Script from 'next/script'
import { Check, CreditCard, ExternalLink, Loader2, Tag, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Separator } from '@/components/ui/Separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { billingApi } from '@/services/api'
import toast from 'react-hot-toast'
import { formatErrorToast } from '@/lib/humanizeError'

declare global {
  interface Window {
    Paddle?: any
  }
}

interface BillingConfig {
  provider: 'paddle' | 'polar' | 'dodo'
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
  polar_customer_id: string | null
  polar_subscription_id: string | null
  polar_discount_code: string | null
  dodo_customer_id: string | null
  dodo_subscription_id: string | null
}

interface DiscountInfo {
  id: string
  name: string | null
  code: string
  type: string // percentage | fixed
  basis_points: number | null // percentage: 5000 = 50%
  amount: number | null // fixed: minor units
  currency: string | null
  duration: string // once | forever | repeating
  duration_in_months: number | null
  starts_at: string | null
  ends_at: string | null
  max_redemptions: number | null
  redemptions_count: number | null
}

interface DiscountState {
  provider: string
  code: string | null
  valid: boolean
  discount: DiscountInfo | null
}

function describeDiscount(d: DiscountInfo): string {
  const value =
    d.type === 'percentage' && d.basis_points != null
      ? `${d.basis_points / 100}% off`
      : d.type === 'fixed' && d.amount != null
        ? `${(d.amount / 100).toFixed(2)} ${(d.currency || 'usd').toUpperCase()} off`
        : 'Discount'
  const duration =
    d.duration === 'forever'
      ? 'every payment'
      : d.duration === 'repeating'
        ? `for ${d.duration_in_months ?? '?'} months`
        : 'on your first payment'
  return `${d.name ? `${d.name} — ` : ''}${value}, applies ${duration}`
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
  on_hold: { label: 'Payment failed', variant: 'destructive' },
  failed: { label: 'Failed', variant: 'destructive' },
  pending: { label: 'Pending', variant: 'secondary' },
  cancelled: { label: 'Canceled', variant: 'outline' },
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
  const [discount, setDiscount] = useState<DiscountState | null>(null)
  const [discountInput, setDiscountInput] = useState('')
  const [discountBusy, setDiscountBusy] = useState(false)

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
      if (cfg.data.provider === 'polar') {
        billingApi.discount().then((res) => setDiscount(res.data)).catch(() => {})
      }
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
    if (config.provider === 'paddle' && paddleReady && window.Paddle && priceId) {
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
    // Polar + Paddle fallback: server-created hosted checkout → redirect
    setBusyTier(tier)
    billingApi.checkout(tier)
      .then((res) => {
        if (res.data.checkout_url) window.location.href = res.data.checkout_url
        else toast.error('Checkout URL unavailable')
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

  const applyDiscount = async () => {
    const code = discountInput.trim()
    if (!code) return
    setDiscountBusy(true)
    try {
      const res = await billingApi.setDiscount(code)
      setDiscount(res.data)
      setDiscountInput('')
      if (res.data.code && res.data.valid) toast.success('Discount code applied')
      else if (res.data.code) toast('Code saved — Polar does not recognize it yet')
      else toast.success('Discount code cleared')
    } catch (e) {
      toast.error(formatErrorToast('Could not save the discount code', e))
    } finally {
      setDiscountBusy(false)
    }
  }

  const removeDiscount = async () => {
    setDiscountBusy(true)
    try {
      const res = await billingApi.clearDiscount()
      setDiscount(res.data)
      toast.success('Discount code removed')
    } catch (e) {
      toast.error(formatErrorToast('Could not remove the discount code', e))
    } finally {
      setDiscountBusy(false)
    }
  }

  if (loading) {
    return <div className="flex justify-center p-12"><Loader2 className="h-8 w-8 animate-spin" /></div>
  }

  const status = STATUS_LABELS[sub?.subscription_status || 'none'] || STATUS_LABELS.none
  const currentTier = sub?.plan_tier || 'free'

  return (
    <div className="space-y-6 p-6 max-w-5xl">
      {config?.provider === 'paddle' && config.configured && config.client_token && (
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
              Billing is not configured yet — set the {config?.provider === 'polar' ? 'POLAR_ACCESS_TOKEN / POLAR_PRODUCT_*' : config?.provider === 'dodo' ? 'DODO_PAYMENTS_API_KEY / DODO_PRODUCT_*' : 'PADDLE_API_KEY / PADDLE_CLIENT_TOKEN'} variables to enable checkout.
            </p>
          )}
          <div className="flex gap-3">
            {(sub?.paddle_customer_id || sub?.polar_customer_id || sub?.dodo_customer_id || (config?.provider && config.provider !== 'paddle')) && config?.configured && (
              <Button variant="outline" onClick={openPortal} disabled={portalLoading}>
                {portalLoading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <CreditCard className="h-4 w-4 mr-2" />}
                Manage billing
                <ExternalLink className="h-3 w-3 ml-1" />
              </Button>
            )}
            {(sub?.paddle_subscription_id || sub?.polar_subscription_id || sub?.dodo_subscription_id) && sub?.subscription_status === 'active' && (
              <Button variant="outline" onClick={cancel}>
                <XCircle className="h-4 w-4 mr-2" />
                Cancel subscription
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Separator />

      <Tabs defaultValue="plans">
        <TabsList className="mb-4">
          <TabsTrigger value="plans">Plans</TabsTrigger>
          {config?.provider === 'polar' && (
            <TabsTrigger value="discount">
              <Tag className="mr-2 h-4 w-4" />
              Discount
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="plans">
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
        </TabsContent>

        {config?.provider === 'polar' && (
          <TabsContent value="discount">
            <Card>
              <CardHeader>
                <CardTitle>Discount code</CardTitle>
                <CardDescription>
                  Saved on your team and applied automatically to your next Polar checkout
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {discount?.code && (
                  <div className="flex items-center justify-between rounded-lg border p-3">
                    <div>
                      <p className="font-mono font-medium">{discount.code}</p>
                      {discount.discount ? (
                        <p className="text-sm text-muted-foreground">
                          {describeDiscount(discount.discount)}
                        </p>
                      ) : (
                        <p className="text-sm text-amber-600">
                          Polar does not recognize this code yet — it will be ignored at checkout.
                        </p>
                      )}
                    </div>
                    <Badge variant={discount.valid ? 'default' : 'secondary'}>
                      {discount.valid ? 'Active' : 'Not active'}
                    </Badge>
                  </div>
                )}
                <div className="flex items-end gap-2 max-w-md">
                  <div className="flex-1 space-y-2">
                    <Label htmlFor="discount-code">
                      {discount?.code ? 'Replace code' : 'Enter a code'}
                    </Label>
                    <Input
                      id="discount-code"
                      value={discountInput}
                      onChange={(e) => setDiscountInput(e.target.value.toUpperCase())}
                      placeholder="e.g. LAUNCH20"
                      autoComplete="off"
                      disabled={discountBusy}
                    />
                  </div>
                  <Button onClick={applyDiscount} disabled={discountBusy || !discountInput.trim()}>
                    {discountBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Apply'}
                  </Button>
                  {discount?.code && (
                    <Button variant="outline" onClick={removeDiscount} disabled={discountBusy}>
                      Remove
                    </Button>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  Codes are managed in your Polar dashboard — percentage or fixed amount, one-time
                  or recurring. The code is checked again at checkout, so expired or exhausted
                  codes are simply skipped.
                </p>
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
