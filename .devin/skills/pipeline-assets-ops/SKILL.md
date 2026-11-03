---
name: pipeline-assets-ops
description: >
  The Cloudless pipeline toolkit (Founders 3-11-2026 day2 folder,
  OneDrive/Kakkava Sofia/Founders) adopted into the stack — the €800 /
  14-day Letter of Agreement generator, the quiet-lead follow-up sequence
  (24h nudge → 3d pain → 7d clean break) wired into the daily nurture
  sweep, newsletter framework seeds, the sales/conversation script canon
  (pitches, openers, objection handlers), the 30-min audit worksheet
  generator, the 14-day onboarding checklist, and the Implementation Kit
  autoresponder (now the nurture ladder). Use when closing an SMB deal,
  reviving a quiet lead, running an audit call, or onboarding a client.
  Tools: backend/scripts/letter_of_agreement.py, quiet_lead_followup.py,
  audit_worksheet.py.
---

# Pipeline Assets Ops — LOA, quiet-lead follow-up, newsletter framework

Source: `OneDrive/Kakkava Sofia/Founders/3-11-2026/Cloudless_Pipeline_Assets.pdf`
(Pipeline Optimization Toolkit). Companions: `signature-offer-ops` (offer
frame), `offer-survey-ops` (pricing validation), `email-campaigns-ops`
(send mechanics), `deploy-seeds` (template upsert contract).

## Asset 1 — Letter of Agreement (the €800 / 14-day engagement)

Fixed scope, fixed price, fixed clock: Web Design + Hosting + Automation,
live in 14 days, **€800 excl. VAT**, 50% deposit to lock the date / 50% on
Day 14. Client owns 100% of the code (Code of Honor: no lock-in).

```bash
# inside social-api (or any env with backend deps — reportlab)
python3 /app/scripts/letter_of_agreement.py --client "Acme SA" --out loa-acme.pdf
python3 /app/scripts/letter_of_agreement.py --client "Acme SA" --format both
# --amount 800 (default) --date today --provider Cloudless
```

Output: a signature-ready 1-page PDF (reportlab) and/or Markdown. Send it
right after the audit call lands a yes — never before scope is confirmed.

## Asset 2 — Quiet-lead follow-up (24h / 3d / 7d)

For leads that expressed interest in the free tech checkup but went quiet.
Copy lives in `app/services/nurture_sequence.py` → `QUIET_STEPS` (nudge →
pain reminder → clean break). The daily `nurture-sweep` beat advances them;
age is measured from `meta_data.quiet_lead_at` (enrollment), not
`created_at` — a stale lead enrolled today starts at touch 1.

```bash
docker exec social-api python3 /app/scripts/quiet_lead_followup.py enroll \
    --email owner@acme.gr --name "Nikos" --company "Acme SA"
docker exec social-api python3 /app/scripts/quiet_lead_followup.py status
docker exec social-api python3 /app/scripts/quiet_lead_followup.py unenroll --email owner@acme.gr
```

Rules:
- Quiet lane **supersedes** the newsletter/audit nurture ladder — a
  quiet-enrolled lead never double-mails.
- `unsubscribed` / `nurture_opted_out` meta flags stop every lane.
- Touch 3 (clean break) is the last contact — after it, the lead goes to
  EspoCRM as cold; do not re-enroll for 90 days.

## Asset 3 — Newsletter framework (3-week rotation)

Seeded as `content_prompt_templates` rows (deploy-seeds contract):

| Week | Theme | Pillar | Core value | CTA |
|---|---|---|---|---|
| 1 | Cost Horror Story | Perspective | small team overpaying 500+/mo → simple tools plug leaks | "Stop overpaying. Book a free 30-min tech checkup" |
| 2 | 14-Day Transformation | Proof | buggy tech → live automation in 14 days, case-study style | "Grab a free 30-minute audit slot" |
| 3 | DIY Audit Checklist | Offer | 3-item self-check (load speed, subscription waste) | "Stuck? Book your free 30-min action plan call" |

Draft issues via the templates (Draft-with-AI on `/campaigns` or
`/api/v1/email-campaigns`) — every issue keeps one idea + one CTA to the
free audit. Rotate weekly; do not send all three to the same audience in
the same week (email-campaigns-ops dedup gate still applies).

## Asset 4 — Sales & Conversation Playbooks (script canon)

`Cloudless_Sales_Playbook.pdf` and `Cloudless_Conversation_Playbook-v2.pdf`
are the same canon (v2 is the simple-English edition). Use verbatim for
DMs, calls, and outreach copy — do NOT paraphrase; the wording is tested.

- **15s intro:** "We help startups and small businesses fix slow daily
  tasks and high software bills. At Cloudless, we design easy websites,
  hosting, and automatic marketing systems in just 14 days flat. You own
  everything we make, and investments start at only €800."
- **60s pitch:** Problem (slow tasks + hidden software bills) → Industry
  flaw (agency retainers / ghosting freelancers) → Cloudless difference
  (14 days, 100% ownership, no lock-in) → Close (€800 start or €3,600/mo
  bundle; 3 new clients/month; book free checkup at cloudless.gr).
- **Outbound openers:** Cold ("Hey [Name]! I came across [Company]… free
  30-min tech checkups this week… Are you free for a quick chat?") and
  Warm/newsletter-subscriber variant. Used by outreach lanes.
- **3-step chat guide:** Ask (opener) → Recommend ("pick a time on my
  site: cloudless.gr") → Agree ("I see your booking!"). Safety valve on
  NO: "No problem at all! …you can always find us at cloudless.gr."
- **Audit call script:** 3 questions in order — Goal → Pain (bills/tools/
  time?) → Timeline — then mirror their words into the Single-Fix
  recommendation, then: "the total is ONLY €800… 50% to start, 50% when
  we finish in two weeks. Should I send you the payment link?" → **stop
  talking, wait for them.**
- **Objection handlers:** every pushback gets a question back, never a
  defense — price→"what range did you have in mind?", can't afford→"the
  total or the split?", discount→"what budget? we can remove tasks",
  think→"what parts need more certainty?", timing→"what would need to
  change?", partner→"what will they need to understand?", send-info→
  "what would be most useful to see?"

## Asset 5 — 30-Minute Audit Worksheet

```bash
python3 /app/scripts/audit_worksheet.py --out audit-worksheet.pdf
python3 /app/scripts/audit_worksheet.py --client "Acme SA" --format both
```

Fillable 1-pager: 3 discovery questions, 6 red flags to check live, the
free action-plan quick wins, and the close script. Print before every
audit call; the completed sheet goes into EspoCRM as the lead's note.

## Asset 6 — 14-Day Onboarding & Fulfillment Checklist

Ops contract once a client says YES — Day 1: send LOA + 50% deposit
invoice, open shared asset folder, confirm-by-text. Days 2–10: build
(simple-text web layout + secure hosting + inquiry→dashboard
automation). Days 11–14: mobile/forms/metrics QC, handoff docs + code,
final 50% invoice, transfer 100% ownership on cleared invoice.

## Asset 7 — Implementation Kit notes

- **Lead magnet:** the 3-Step Cloud Cleanup Playbook (idle subscriptions
  → automate manual work → own code/hosting) = the playbook already
  shipped at `/playbooks/cloud-migration-playbook.pdf`.
- **Autoresponder:** kit's 3-email sequence is now the nurture ladder —
  Email 1 = `send_playbook_email` (day 0), Email 2 = STEPS day 2 (leak
  lesson), Email 3 = STEPS day 4 (14-day clock); day-14 fixed-scope
  closer retained as the last touch. Then leads roll into the quiet lane
  automatically.
- **Copy rule:** plain English only — "fix slow daily tasks and high
  software bills", never "optimized end-to-end infrastructure". Same rule
  the content-quality gate (content-quality-ops) enforces.

## Machine-readable canon

`Cloudless_Agent_Manifest.toml` (this dir) — the single source of truth:
pricing engine, positioning, landing-page schema, booking-form fields, lead
magnet, autoresponder, DM openers/follow-up/safety-valve, LOA template, and
the production checklist. Validate generated copy against it.

## DM lanes (manifest OUTBOUND_MESSAGING + FOLLOW_UP_AUTOMATION)

Verbatim DM copy lives in `app/services/nurture_sequence.py`:
`DM_OPENERS` (cold/warm), `BOOKING_DM_STEPS` (24h → 3d → 4d after a lead
asks for the booking link then goes quiet), `DM_SAFETY_VALVE` (graceful
close on explicit NO). Use verbatim in LinkedIn DM / Messenger / WhatsApp
lanes — never paraphrase; the email twin is `QUIET_STEPS`.

## Onboarding checklist (manifest PRODUCTION_CHECKLIST)

`scripts/onboarding_checklist.py --client "X" --format both` → fillable
PDF/MD of the Day 1-14 ops contract (LOA + deposit → build → QC + handoff).

## Source frameworks (Sofia Kakkava Coaching — day1 sessions)

The day2 assets instantiate these coaching frameworks — validate any
generated offer/LOA/pitch against them:

- **30-second sales pitch template:** "For [buyer] who needs [result], I
  recommend [offer]. It includes [key scope], delivered [timing]. The
  price is [amount and terms]. To proceed, [exact next step]."
  Self-check: can the buyer recognise their need? is the scope specific?
  is the price clear? can they act immediately? (The manifest's audit-call
  close script IS this template filled in.)
- **Complete-offer spec** (QA checklist for LOA + /audit + pricing copy):
  3-5 specific outcomes · delivery format & timing (method) · what the
  buyer must supply → commitment · one important exclusion · one factual
  reason to choose us. Our instance: outcomes = fast site + secure hosting
  + hands-free leads; timing = 14 days; commitment = copy/logos on Day 1;
  exclusion = ongoing retainer (none — ownership is yours); reason = flat
  €800 fixed scope, not hourly agency pricing.
- **Positioning statement:** "For [buyer] who needs [result], [offer]
  provides [specific solution]. Why choose it? [one factual difference.]"

## Funnel liveness gates (pipeline_gap_doctor.py)

Three env/config gates decide whether the funnel actually mails and
captures — one script checks all three:

```bash
# inside social-api (app env + deps), or:
docker run --rm --network cu130-db --env-file .env \
  -v "$(pwd)/social-automation/backend:/app" -w /app \
  ghcr.io/themis128/cu130-slim:social-backend-api-latest \
  python scripts/pipeline_gap_doctor.py
```

- `team` — discovers which team owns public-form leads and prints the
  exact `CLOUDLESS_WEB_ANALYTICS_TEAM_ID=…` .env line + restart command
  (sweep no-ops until set — current live state).
- `audit` — probes the `/api/v1/leads/public` endpoint the site's
  /api/audit forwards to; empty POST must 4xx (live) not 5xx/timeout.
- `db-env` — DATABASE_URL vs `social-postgres` container creds; reports
  MATCH/MISMATCH per field only (never prints secrets). Catches the
  rotated-password drift that broke the seed run.
