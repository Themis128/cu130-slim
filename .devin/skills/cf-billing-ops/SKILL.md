---
name: cf-billing-ops
description: Cloudflare billing guards — enforce the free-first policy. `scripts/observability_guard.py` asserts zero Observability billing before the Dec-2026 deadline; `scripts/obs_cancel.py` cancels paid observability add-ons. Run on schedule or when Cloudflare billings change.
---

# cf-billing-ops

Cloudflare billing guardrails. The stack is Cloudflare-first / free-first —
any paid add-on (Workers Paid, Observability, R2 egress overage) must be
visible and deliberate.

## Scripts

- `scripts/observability_guard.py` — polls the Cloudflare GraphQL/analytics
  billing surface and fails loudly if Workers Observability is enabled or
  billed, so nothing silently flips to paid before the Dec 1 2026 deadline.
  Run it on a cron/watchdog; non-zero exit = breach.
- `scripts/obs_cancel.py` — cancels the Observability add-on subscription
  via the Cloudflare API when the guard trips.
- `scripts/invoice_pay.py` — invoice payment automation (added 2026-11-13).
  `check` reports unpaid invoices (API lane needs a Billing-scoped token —
  the stock `CLOUDFLARE_API_TOKEN` returns auth errors on `/billing/*`;
  falls back to scraping the logged-in dashboard over CDP :9222).
  `pay --yes` calls `POST /accounts/{id}/pay-bad-debt` (or `pay-invoice`
  with a specific id) using `CLOUDFLARE_BILLING_TOKEN` — create one at
  dash.cloudflare.com → My Profile → API Tokens with
  **Account → Billing → Edit**. SCA/3DS challenges return a Stripe
  `client_secret` and stay a human step. Dry-run without `--yes`.

## Notes

- Uses the Cloudflare API token in `.env` (`CLOUDFLARE_API_TOKEN`) — do not
  print it.
- Guard exits 0 when clean; wire it into the daily ops watchdog so paid
  drift is caught within a day.
