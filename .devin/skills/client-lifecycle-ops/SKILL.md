---
name: client-lifecycle-ops
description: Post-won client lifecycle automation for the agency loop — payment-link proposals, won→kickoff+intake, 14-day PM, testimonial→proof, 30/60/90-day delight ladder, support→Case triage, and the remaining human gates. Use when asking "is onboarding automated", "did the client pay", "where do testimonials go", "what happens after delivery", or adding lifecycle steps.
---

# Client lifecycle ops

The automated post-won chain, end state after the funnel-closure work:

```
proposal approve → mail + Stripe payment link → client pays
→ checkout.session.completed → EspoCRM Closed Won deal
→ won_sweep (hourly :35) → AgencyProject + kickoff mail
  (intake checklist + Letter of Agreement inline)
→ client replies → crm_engagement stamps intake_received
  (no reply: day-2 nudge, day-4 Slack escalate)
→ pm_sweep daily: milestones → client-update DRAFTS → day-14 handoff
→ +2d testimonial ask → reply stamps testimonial_received + proof_card
→ +30d check-in · +60d referral · +90d maintenance upsell (_DELIGHT_STEPS)
→ support mail (client + problem keyword) → EspoCRM Case + Slack
```

## Key knobs

- `AGENCY_CHECKOUT_URL` — cloudless.gr `/api/checkout/agency` minter;
  empty = proposals send without a payment link (safe default). Set to
  `https://cloudless.gr/api/checkout/agency`.
- `CLOUDLESS_LEADS_WEBHOOK_SECRET` — shared secret for the minter call
  (same lane as /api/webhooks/socialauto-leads).
- `AGENCY_STRIPE_PROJECT_MIN_EUR` (500) — Stripe checkouts below this
  never become projects (digital goods ≠ consulting engagement).
- All stamps live in `AgencyProposal.meta_data` /
  `AgencyProject.meta_data`: `payment_url`, `loa_md`, `intake_received`,
  `intake_nudged`, `intake_escalated`, `testimonial_due_at`,
  `testimonial_asked`, `testimonial_received`, `proof_card`,
  `support_cases[]`, `delight_{checkin_30,referral_60,upsell_90}`.

## Human gates that stay (agentic-bpm review autonomy)

- Proposal SEND — approve/reject in Slack; `AGENCY_PROPOSAL_AUTOSEND` off.
- Client milestone updates — drafted by pm_sweep, sent by a human.
- Publishing a `proof_card` testimonial — draft + Slack surface only.
- Engineering execution — `engineer_sweep` drafts PR manifests; humans
  write/merge code.
- Acting on credentials clients send during intake.

## Remaining manual gates (platform-level, can't automate)

`pending-inputs-ops` tracks: X OAuth re-auth (browser consent click —
`x-oauth-wire` + `totp-ops` cover most of it), WhatsApp phone verify
(`whatsapp-ops` auto-verify retries), LinkedIn sidecar session seeds
(`session-ops`), Qualtrics access forms (`qualtrics-ops` fills them via
CDP), LinkedIn Member DP review (≤21d wait, external).

## Known gaps (next increments, in BACKLOG)

- Custom-scope proposals: audits whose services don't map to the catalog
  currently skip drafting — a `needs_pricing` draft + Slack gate is the
  design, not yet built.
- Site content pipeline: audits/handoffs never become AppFlowy drafts
  (blog/case-study) — proof_card is the first surface; auto-publish
  stays gated.
- `proof_card` → AppFlowy testimonials publish is a human step today;
  an /api/admin/appflowy write endpoint could automate it once the
  review gate is lifted.
