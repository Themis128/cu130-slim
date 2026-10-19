---
name: leadgen-ops
description: >-
  Lead-generation readiness for SocialAuto — adopted from Meta's "10
  strategies for social media lead generation" (facebook.com/business/
  resources/social-media-lead-generation). Covers scripts/leadgen_audit.py —
  live checks of capture surfaces (contact/pricing/links probes), per-account
  DM lead lanes (auto-reply flags), UTM CTA discipline on published posts,
  and 30d lead volume by source from EspoCRM. Use when asking "are we
  actually capturing leads", before launching traffic/promo pushes, or when
  the funnel top looks busy but the CRM stays empty.
---

# Leadgen-ops

Meta's guide framed the right question: *qualified leads at sustainable
cost*. This skill checks that every lead-capture lane the guide names is
actually wired — not that we're merely posting.

## Strategy mapping (honest)

| Meta strategy | SocialAuto surface | Status |
|---|---|---|
| 1. Lead magnets | `cloudless.gr/links` 12-Automation Checklist | live — probed |
| 2. Profile→capture | bio/website fields per account | partial — only TikTok bio is cached; others show "bio not cached" (verify via platform or social-profile-update) |
| 3. Strong CTAs | canonical UTM pattern + NLP gate | audited — % of linked posts tagged |
| 4. Retarget warm audiences | Meta Ads Manager | **paid — out of scope (no-spend rule)** |
| 5. Social proof | content voice (testimonials/counts) | content guidance — social-content-core |
| 6. Video lead content | branded-video-pipeline skill | available |
| 7. Landing pages | `cloudless.gr/contact`, `/pricing` | live — probed |
| 8. Lead ads (instant forms) | Meta lead ads | **paid — out of scope** |
| 9. Frictionless forms | contact form minimal fields | manual check (not audited) |
| 10. Advantage+ AI | Meta ads | **paid — out of scope** |

## The tool — `scripts/leadgen_audit.py`

```bash
docker exec social-api python scripts/leadgen_audit.py          # report
docker exec social-api python scripts/leadgen_audit.py --json   # digests
```

Checks (all live, no heuristics):

- **Capture surfaces** — HTTP-probes `cloudless.gr/contact`,
  `social.cloudless.gr/pricing`, `cloudless.gr/links`. Down → gap.
- **DM lead lanes** — per active `social_accounts` row, the platform's
  `*_auto_reply`/bot flag enabled in `meta_data`. A DM is a lead signal per
  the guide ("reaching out in a DM"); an unanswered-DM account is a dead lane.
- **Profile funnel links** — where the platform caches a bio/website in
  `meta_data` (TikTok today), verifies it points at `cloudless.gr`. Absent
  cache → "bio not cached", honest unknown — not a failure.
- **CTA discipline** — `%` of published posts (30d, carrying a link) that
  carry `utm_source=`. <80% → gap (the funnel→CRM-loop rule).
- **Lead volume 30d** — EspoCRM `Lead` count by `source` — Meta's "lead
  volume" metric, measured in the CRM not platform dashboards.

**Exit 1** when a surface is down, a DM lane is missing, or UTM discipline
is broken. Run it before any traffic push — per the funnel-ops auction rule,
traffic into a broken capture path just raises cost-per-lead.

## Reading the output

- `gaps: none` + leads flowing → capture machinery is healthy; effort goes to
  nurture (funnel-ops weakest stage), not more capture plumbing.
- Surface down → fix the site/tunnel first; every post CTA dead-ends.
- DM lane missing → enable `*_auto_reply` for that account (session-ops /
  channel skill).
- UTM <80% → check the generation path — posts must ship CTAs tagged
  `utm_source=<platform>&utm_medium=social&utm_campaign=<slug>`.

## Relationship to funnel-ops

`funnel_audit.py` measures the funnel; `leadgen_audit.py` verifies the
capture *machinery* feeding it. Sequence: leadgen green → funnel weakest
link → trendwatch for topics → post with UTM CTA → leads appear in the
30d-by-source line here. That's the closed loop.
