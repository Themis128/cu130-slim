---
name: cross-channel-ops
description: >-
  Cross-channel campaign-integration audit for SocialAuto — adopted from
  Yulian Prakosos, Ukar & Astutik, "Strategic Significance: Unveiling the
  Dominant Role of Social Media as a Primary Marketing Tool" (ADMAN, vol.
  2 no. 1, April 2024, DOI 10.61100/adman.v2i1.137). Covers
  scripts/crosschannel_audit.py — the paper's integration rule (social is
  part of a holistic strategy, not a standalone channel) enforced as ONE
  utm_campaign slug per campaign across social+email+ads, CTA-tagging
  coverage, attribution-loop liveness, and the influencer/partner surface.
  Use when asking "are our channels working together", checking campaign
  attribution, or planning a multi-channel push.
---

# Cross-channel-ops — ADMAN integration discipline on live data

Source: Riyan Dwi Yulian Prakosos, Kurweni Ukar, Wahyuni Sri Astutik —
*Strategic Significance: Unveiling the Dominant Role of Social Media as a
Primary Marketing Tool in Digital Business Strategies* (Journal of
Contemporary Administration and Management / ADMAN, vol. 2 no. 1, April
2024, pp. 363–370, DOI 10.61100/adman.v2i1.137). PDF:
`OneDrive/Kakkava Sofia/Papers/2928dbbb7071064dca0f02236e0c0ef894ca.pdf`.

## The paper's model

A qualitative lit-review (Google Scholar 2012–2024) arguing social media is
the *primary* marketing tool — but only when treated as part of an
integrated strategy. Five recommendations:

1. **Relevant, high-quality content** across formats — content quality is
   the cornerstone (→ `social-content-core`, Sofia Kakkava rules).
2. **Analytics tools** to monitor performance and decide (→ this audit +
   `engagement-ops` + strategy briefs).
3. **Influencer & strategic-partner collaboration** — tap loyal fan bases
   and complementary brands.
4. **Integration with other marketing efforts** — consistent brand
   experience across channels; social is not a standalone entity.
5. **Responsiveness to change** — algorithms and platform policies shift;
   monitor and adapt (→ `adaptive-rate-limiting`, session-ops).

Its hardest named challenge: **measuring ROI** — attribution, not vanity.

## Our map → live surfaces

| Paper lever | SocialAuto surface | Measured from |
|---|---|---|
| channel integration | ONE `utm_campaign` slug per campaign, shared across social+email+ads | `web_analytics_events` payload + `email_campaigns` cta_url |
| ROI/attribution | UTM-tagged CTA → site event → EspoCRM lead | `web_analytics_events` 30d count |
| content quality | NLP score ≥80 + quality pipeline | `social-content-core` rules |
| influencer/partner | none — unbuilt capability | honest "unbuilt" status (backlogged) |
| responsiveness | adaptive rate limiting, session healing | `adaptive-rate-limiting`, `session-ops` |

**The slug rule** (already standing, PR #752): one campaign = one
`utm_campaign` value everywhere. `utm_source`/`utm_medium` identify the
channel; `utm_content` the asset. `tag_owned_links` in
`app/services/utm.py` applies it at publish; email `cta_url` meta carries
it for campaigns.

## The tool

```bash
docker exec social-api python scripts/crosschannel_audit.py          # human report
docker exec social-api python scripts/crosschannel_audit.py --json   # digests
```

- **Slug inventory** — every `utm_campaign` observed, grouped by channel
  (social events vs email CTAs vs other).
- **Shared-slug check** — a slug appearing on >1 channel = integration;
  zero shared = fragmented campaigns.
- **Coverage** — % of email campaigns with a tagged CTA; % of published
  posts carrying an owned-domain link (all tagged at publish).
- **Attribution liveness** — UTM events in the last 30d.
- **Gaps → exit 1**: no shared slugs, email tagging <90%, CTA coverage
  <50%, attribution silent.

## First run (2026-10-20)

```
shared slugs: —  |  email-only: aie-course, first-approach, renewal
events: build-in-public ×2
email CTA tagged 63.6% | posts with CTA 29.9% (167 posts/30d)
gaps: no shared slugs, tagging 63.6%, CTA coverage 29.9%
```

Honest readings:

- **Zero shared slugs is the real finding** — the `aie-course` email drip
  and social posts never reference the same campaign, so a subscriber who
  also saw the LinkedIn post attributes twice as unrelated campaigns.
  Per the rule, the next campaign launch should pick ONE slug
  (e.g. `aie-course`) and use it in the email CTA AND the social posts
  promoting it.
- **`build-in-public`** is the only social slug that produced site events
  in 30d — 2 events. Attribution machinery works but is starved: only
  ~30% of posts carry a CTA link at all.
- **Email tagging 63.6%** — campaigns tag in EITHER `meta_data.cta_url`
  OR `html_body` links (`&amp;`-encoded); the audit checks both. The 4
  untagged are all already-`sent` campaigns — left as honest history, not
  retro-edited. All current drafts carry a tagged CTA (the two drafts
  with body-only tags had `meta.cta_url` backfilled 2026-10-20).

## Dispatch rules

| Gap | Fix → skill |
|---|---|
| no shared slugs | pick one slug at campaign design time → `content-strategy`, `email-campaigns-ops` |
| email CTA untagged | add utm_campaign to `cta_url` meta → `email-campaigns-ops` |
| low post CTA coverage | posts lack owned-domain links → `social-content-core` CTA discipline |
| attribution silent | loop broken upstream → `leadgen-ops` |
| influencer lane | unbuilt — needs a real Nextcloud/CRM surface before any partnership outreach |

## Relationships

- `leadgen-ops` verifies capture surfaces exist; this verifies the
  *campaign-coherence* layer above them (one slug, all channels).
- `engagement-ops` measures per-channel effectiveness; this measures
  whether channels combine into one attributable campaign.
- `email-campaigns-ops` owns the email send mechanics; this audits the
  UTM discipline of what it sends.
