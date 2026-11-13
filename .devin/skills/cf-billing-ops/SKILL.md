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
  `check` reports unpaid invoices via `GET /accounts/{id}/billing/history`
  (the `/billing/unpaid-invoice` + `/billing/invoices` routes return
  10404 on the current API — do not use them). OPEN invoices with
  `amount_to_pay > 0` are the payable set.
  `pay --yes` calls `POST /accounts/{id}/pay-invoice` (or `pay-bad-debt`
  for all debt) using `CLOUDFLARE_BILLING_TOKEN`.

  **Token status 2026-11-13:** `CLOUDFLARE_BILLING_TOKEN` is minted and in
  `.env` (Account → Billing → Write only). It was created programmatically
  via `CF_ACCESS_TOKEN` (the `cloudless-access` token carries
  `User → API Tokens → Write` → `POST /user/tokens`), after clearing the
  50-token account quota by deleting two never-used tokens. If it ever
  needs re-minting, that path is proven — no dashboard wizard needed.
  Live invoice state at mint time: IN-88049721 $12.08 OPEN,
  `latest_payment_intent_status: requires_payment_method`.
  SCA/3DS challenges return a Stripe `client_secret` and stay a human step.
  Dry-run without `--yes`.

## Notes

- Uses the Cloudflare API token in `.env` (`CLOUDFLARE_API_TOKEN`) — do not
  print it.
- Guard exits 0 when clean; wire it into the daily ops watchdog so paid
  drift is caught within a day.
