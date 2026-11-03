---
name: pipeline-assets-ops
description: >
  The Cloudless Pipeline Optimization Toolkit (Founders 3-11-2026,
  OneDrive/Kakkava Sofia/Founders) adopted into the stack — the €800 /
  14-day Letter of Agreement generator, the 3-part quiet-lead follow-up
  sequence (24h nudge → 3d pain reminder → 7d clean break) wired into the
  daily nurture sweep, and the 3-week pipeline newsletter framework seeded
  as content_prompt_templates. Use when closing an SMB deal (LOA),
  reviving a lead that went quiet, or drafting the weekly newsletter.
  Tools: backend/scripts/letter_of_agreement.py, backend/scripts/quiet_lead_followup.py.
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
