---
name: engagement-ops
description: >-
  Engagement-effectiveness audit for SocialAuto — adopted from Bening &
  Roostika, "Optimizing Social Media and Paid Advertising in Digital
  Marketing Strategy" (Kontigensi Journal, vol. 13 no. 1, June 2025,
  DOI 10.56457/jimk.v13i1.765). Covers scripts/engagement_audit.py — live
  measurement of the paper's three success KPIs (engagement rate,
  conversion rate, customer feedback) plus its five optimization levers
  (interactivity, schedule consistency, trend usage, periodic paid-ads
  evaluation, audience-stage fit). Use when asking "is our social media
  actually working", comparing platform engagement, checking publish
  cadence, or deciding where the next content effort goes.
---

# Engagement-ops — Bening & Roostika KPI triad on live data

Source: Pijar Elsanti Surya Bening, Raden Roro Ratna Roostika — *Optimizing
Social Media and Paid Advertising in Digital Marketing Strategy to Increase
Branding and Customer Engagement PT. KB Valbury Sekuritas* (Kontigensi:
Jurnal Ilmu Manajemen, vol. 13 no. 1, June 2025, pp. 409–423, DOI
10.56457/jimk.v13i1.765). PDF: `OneDrive/Kakkava Sofia/Papers/765-Article
Text-3399-1-10-20250630.pdf`.

## The paper's model

- **Three success KPIs**: engagement rate (likes/comments/shares/views/DM
  interactions), conversion rate (audience → real action: registration,
  transaction), customer feedback (what active customers actually say).
- **Four challenges**: content relevance to the *local* audience, the
  first-few-seconds hook, creative differentiation vs competitors,
  human/technical resource limits.
- **Five optimizations**: (1) interactive formats — live streams, polls,
  Q&A, gamification, educational short video; (2) structured upload
  schedule + social-media trend usage (viral formats, carousels, trending
  audio); (3) build a skilled marketing team; (4) paid ads on **all**
  relevant platforms, evaluated periodically — not just one channel;
  (5) central↔local synergy — adapt campaigns to the local market.
- **The event-gating trick**: webinar/class registration requires
  following the social accounts — converts event participants into
  followers (our analog: the 12-Automation Checklist lead magnet on
  `cloudless.gr/links`).

## Our map → live surfaces

| Paper KPI/lever | SocialAuto surface | Measured from |
|---|---|---|
| engagement rate | per-post metrics | `post_analytics_snapshots` latest-per-post, 30d |
| conversion | UTM-tagged CTA → site → CRM | `web_analytics_events` payload `utm_source` + EspoCRM `Lead.createdAt` |
| customer feedback | comments + shares volume | `post_analytics_snapshots.comments/.shares` |
| interactivity | response-inviting copy | `posts.content_text` scan — `?`, polls, "comment below", votes |
| consistency | publish cadence | `post_targets.published_at` per platform, 28d |
| paid-ads eval | daily Campaign Manager scrape | `ad_campaign_snapshots` recency (<48h = fresh) |
| skilled team | the agent fleet | `agent-workforce` / `scripts/workforce_audit.py` |
| all-platforms paid | promo-credit-only rule | LinkedIn ads live; TikTok/Meta ads intentionally absent (no-spend rule) |

## The tool

```bash
docker exec social-api python scripts/engagement_audit.py          # human report
docker exec social-api python scripts/engagement_audit.py --json   # digests
```

- Per-platform engagement rate, comment/share feedback volume (30d).
- Conversion line: UTM events + fresh EspoCRM leads (30d).
- Levers: % of published copy inviting a response, posts/week per
  platform, paid-eval snapshot freshness.
- **Gaps → exit 1**: ER <0.5% on ≥3 posts, silent active platform (28d),
  interactivity <40%, stale/missing paid-ads snapshots, zero UTM events.

## First run (2026-10-20)

```
facebook 1.25% ER | instagram 19.7% | linkedin ~100%* | threads 1.33%
tiktok 0.18% | twitter 4.26%
conversion: 4 UTM events, 179 leads (30d) | interactivity 49.4%
paid-eval: fresh (17.7h)
gaps: tiktok ER 0.18%, telegram + whatsapp silent 28d
```

Honest readings:

- **LinkedIn ~100% ER is a data artifact**, not a result — snapshot
  engagement ≈ impressions means the source sync stores cumulative or
  mis-scoped metrics (same class of corruption as follower-analytics-guard).
  Trust the direction (comments=1995 is real volume), not the ratio.
- **Telegram silence is likely real failure** — the
  `cloudless-telegram-channel` n8n workflow is active (Mon/Wed/Fri) yet
  zero publishes in 28d; check its executions.
- **WhatsApp silence is by design** — messaging channel, reply/event-driven,
  not a feed. Excluded from cadence expectations mentally; the audit lists
  it for completeness.
- **TikTok 0.18%** matches the known publish blockers (unaudited client,
  spam_risk drafts, MEDIA_PUBLIC_BASE_URL) — the platform barely reaches
  audiences right now.

## Dispatch rules when the audit flags a gap

| Gap | Likely cause → skill |
|---|---|
| low ER on a platform | content not landing or publish chain degraded → `social-content-core`, `*-content-ops`, `strategy-brief-triage` |
| silent platform | workflow/session broken → `n8n-cloudless` executions, `session-ops` |
| interactivity <40% | copy doesn't invite responses → `content-strategy` (post formula), Sofia Kakkava CTA rules |
| paid-eval stale | `linkedin-ads-report` beat dead → `agent-workforce`, `linkedin-ads-ops` |
| zero UTM events | attribution loop broken → `leadgen-ops` (its attribution check) |
| LinkedIn-style ER artifact | snapshot source corrupt → `follower-analytics-guard` methodology |

## Relationships

- `funnel-ops` measures *where leads die* (stage-to-stage); this measures
  *whether the content engine produces engagement+conversion at all*.
- `leadgen-ops` verifies capture machinery; this verifies the upstream
  demand the capture depends on.
- `strategy-brief-triage` interprets the daily brief; this is the
  weekly/monthly health check that confirms its recommendations land.
