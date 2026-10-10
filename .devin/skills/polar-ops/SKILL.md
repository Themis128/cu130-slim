---
name: polar-ops
description: Polar.sh billing operations for SocialAuto — the polar_api.py client, /api/v1/billing endpoints (checkout, portal, subscription, webhook), the hosted Polar MCP server (live products/orders/customers), sandbox vs production, and checkout-link for digital downloads. Use for billing config, checkout/portal issues, webhook debugging, product/price changes, or revenue/subscription questions.
---

# Polar ops

Polar.sh is the Merchant-of-Record billing backend for SocialAuto plans
(`BILLING_PROVIDER=polar`; alternates `paddle`, `dodo` exist behind the same
billing router). The funnel: all brand CTAs close at
`https://social.cloudless.gr/pricing` → Polar hosted checkout.

## Backend integration (already live)

- `app/services/polar_api.py` — thin async httpx wrapper over
  `https://(sandbox-)api.polar.sh/v1`: checkout sessions, subscriptions,
  customer portal sessions, Standard Webhooks signature verification
  (`webhook-id`/`-timestamp`/`-signature`, 5-min tolerance).
- `app/api/billing.py` — endpoints (mutating ones require team OWNER):
  `GET /config`, `GET /plans`, `GET /subscription`, `GET /discount`,
  `POST /checkout`, `POST /portal`, `POST /cancel`, `POST /sync`,
  `POST /polar-webhook` (idempotent via `billing_events` table).
- Env: `POLAR_ENVIRONMENT` (sandbox|production), `POLAR_ACCESS_TOKEN`
  (org access token, server-side only), `POLAR_WEBHOOK_SECRET`,
  `POLAR_PRODUCT_PRO/BUSINESS/ENTERPRISE` (product id → plan tier map).
- Plan limits enforced via `app/core/quotas.py` `PLAN_LIMITS`; admin team is on
  `enterprise` (unlimited) per global rules — quota blocks on admin are bugs.

## Polar MCP server (hosted)

Registered as `polar` in `.devin/mcp_config.json` via
`.devin/skills/polar-ops/scripts/polar-mcp.sh` — an mcp-remote wrapper that
pulls `POLAR_ACCESS_TOKEN` from the repo `.env` and passes it as a Bearer
header to `https://mcp.polar.sh/mcp/polar-mcp`. No OAuth flow, no committed
secret.

The server uses lazy tool discovery: `search_tools` → `describe_tools` →
`execute_tool`. Use it for live reads of products, prices, orders,
subscriptions, customers, benefits, checkouts, discounts, license keys,
refunds, and org metrics instead of hand-writing API calls.

## Sandbox vs production

- Dashboards: `https://sandbox.polar.sh` / `https://polar.sh` — fully
  isolated orgs; sandbox tokens do NOT work against production.
- `POLAR_ENVIRONMENT` switches the API base URL; the MCP token governs which
  org the `polar` MCP sees (create a separate token per env).
- Webhook testing: `npx @polar-sh/cli listen --to http://localhost:8083/api/v1/billing/polar-webhook`
  forwards live events locally; `polar trigger` resends fixtures.

## Selling digital downloads

`npx checkout-link <file>` (polarsource/checkout-link) generates a Polar
checkout link for a one-off file sale — useful if a paid template/asset ever
gets promoted via social posts. The monthly automation-checklist.pdf stays a
free lead magnet (see `monthly-checklist-update` skill).

## Upstream references (polarsource org)

- `polar` (main monorepo) — self-hostable but we use the SaaS MoR.
- `polar-python` — official SDK; our `polar_api.py` is a tailored thin client —
  adopt the SDK only if we need benefit grants / usage metering models.
- `polar-ingestion` — usage-based-billing ingestion framework (TS); relevant
  if SocialAuto ever bills per-AI-call.
- `polar-adapters` — framework checkout helpers (Next.js etc.); relevant if
  cloudless.gr migrates Stripe→Polar (see polar-migration skill upstream).
- `skills` repo — Polar's own agent skills: polar-integration, polar-migration
  (Stripe/Paddle/LS→Polar playbook), polar-testing (sandbox + CLI webhook
  testing), setup-polar.

## Troubleshooting

- Webhook rejected → verify `POLAR_WEBHOOK_SECRET` matches the endpoint
  secret on the Polar dashboard and the clock skew < 5 min.
- Checkout 401/403 → wrong env token for `POLAR_ENVIRONMENT`.
- Plan not upgrading after payment → check `billing_events` for the
  `checkout.completed` event, then `POST /billing/sync` to re-pull the
  subscription state.
- `polar` MCP dead → verify `mcp-remote` installed
  (`~/.local/lib/mcp-remote/node_modules/mcp-remote/dist/proxy.js`) and
  `POLAR_ACCESS_TOKEN` present in `.env`.
