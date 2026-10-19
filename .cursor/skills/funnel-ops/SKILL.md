---
name: funnel-ops
description: >-
  Sales-funnel stage audit for SocialAuto — adopted from Tolstova & Silakova,
  "Classification of management tools for building a sales funnel in small
  businesses" (Omsk Scientific Bulletin 2023, DOI 10.25206/2542-0488-2023-8-2-145-154).
  Covers scripts/funnel_audit.py — live stage counts from Postgres + EspoCRM
  (audience → engaged → lead → nurtured → opportunity → won), stage-to-stage
  conversion, weakest-link detection, and the auction warning (traffic growth
  without conversion erodes margin). Use when asking "is the funnel working",
  "where do leads die", planning lead magnets, or deciding whether to buy
  traffic vs fix conversion.
---

# Funnel ops — Tolstova & Silakova stage model on live data

Source: Э. Р. Толстова, Л. В. Силакова — *Классификация инструментов
управления в организациях малого бизнеса для построения воронки продаж*
(Omsk Scientific Bulletin, Society. History. Modernity, vol. 8 no. 2, 2023,
pp. 145–154, DOI 10.25206/2542-0488-2023-8-2-145-154). PDF:
`OneDrive/Kakkava Sofia/Papers/145-154++Толстова+Э.+Р.,+Силакова+Л.+В..pdf`.

## The paper's model

- **10-stage customer journey**: brand awareness → problem awareness →
  solution search → evaluation → purchase decision → purchase → trial →
  use → upsell → advocate.
- **Quantitative funnel** (Tokarev variant): each stage has a real headcount
  (e.g. 10 000 audience → 1 000 interested → 100 discussing → 80 booked →
  70 paid → 10 regulars → 20 referrals). Conversion is measured
  stage-to-stage, never stage-to-zero.
- **9-step build process**: model the funnel → automate processes →
  scripts/regs for staff → brand development → conversion analytics &
  traffic arbitrage → scale → tight audience focus → USP → lead magnet.
- **Tool classification**: management systems (ERP/CASE/CALS) → BPM modeling
  → CRM → social-analytics services, mapped to funnel stages by scale.
- **Two operational warnings**: (1) the funnel's value is locating and fixing
  the *weakest* stage — "instantly eliminate weak points"; (2) buying more
  traffic follows an auction — each extra visitor costs more, so growing the
  top while conversion is broken erodes margin.

## Our stage map → live surfaces

| Paper stage | SocialAuto surface | Counted from |
|---|---|---|
| audience (1) | follower base | `follower_snapshots` latest per account |
| engaged (2-3) | posts earning reactions | `post_analytics_snapshots`, likes+comments+shares>0, 30d |
| lead (4-5) | captured leads | EspoCRM `Lead` rows by status/source |
| nurtured (6-7) | leads past `New` + drip mail | EspoCRM statuses + `email_logs` |
| opportunity (8) | active deals | EspoCRM `Opportunity` |
| won / advocate (9-10) | closed-won, repeat | EspoCRM `Opportunity` stage |

## The tool

```bash
docker exec social-api python scripts/funnel_audit.py          # human report
docker exec social-api python scripts/funnel_audit.py --json   # digests
```

- Prints each stage's live count + stage→stage conversion %.
- Names the **weakest transition** (the paper's weak-point rule).
- **Auction warning** fires when reach exists but lead conversion < 0.5% —
  more traffic costs more per lead before it buys revenue.
- Exit 1 when a stage has zero count with live upstream volume — a broken
  stage, not noise.

## First-run finding (2026-10-19)

```
audience 1006 → engaged 92 (9.2%) → lead 179 → nurtured 0 (0%)
→ opportunity 0 → won 0
weakest: lead→nurtured (0.0%)   lead sources: Newsletter=168, Web Site=9
```

Real gap the audit exposes: **capture works, nurture doesn't** — 179 leads
sit at `New`, `crm-lifecycle` is idle in the workforce audit, and no lead
has ever advanced. Newsletter leads (168 of 179) have no email → the drip
engine can't reach them; their nurture lane is LinkedIn DM/invite (see
`espocrm-ops`).

## Dispatch rules when the audit flags a stage

| Weakest transition | Likely cause → skill |
|---|---|
| audience→engaged | content not landing → `social-content-core`, `strategy-brief-triage` |
| engaged→lead | CTA/capture broken → check UTM links, `/contact` → EspoCRM writes (`espocrm-ops`) |
| lead→nurtured | lifecycle sweep or email reachability → `espocrm-ops`; email-less newsletter leads need the DM lane |
| nurtured→opportunity | no offer/qualification step → lead magnet or audit CTA (`content-strategy`) |
| opportunity→won | sales motion — human, not automation |
