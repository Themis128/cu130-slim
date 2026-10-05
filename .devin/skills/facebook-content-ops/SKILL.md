---
name: facebook-content-ops
description: >-
  What to publish on Cloudless Facebook — the personal professional-mode
  profile (raw build-log content, FB Stars funnel to 500 followers) and the
  canonical cloudless.gr page (brand voice, Messenger bot). Covers the
  sidecar-only personal-profile path, Graph page posting, the deleted
  duplicate page, and Stars monetization status. Use for any FB post,
  schedule, or monetization check.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Facebook Content Ops

Read `social-content-core` first.

## Surfaces

| Account | ID | Type | Job |
|---------|----|------|-----|
| Personal | `9355ed63-7787-43e5-a22d-ae0a33d5176b` | `account_type=user`, professional mode ON | **The unpolished build log** + Stars monetization |
| Page (canonical) | `ad83c946-0f6b-4fbf-bc63-543d2c2237f5` | page ID 116436681562585 | Brand posts; **Messenger bot config + subscription live here** |
| ~~Page (duplicate)~~ | `e280d96f-2807-4150-babe-f388d02bcfba` | status=revoked | **DELETED 2026-10-02 — never target, never "fix"** |

## Content rules

### Personal profile (`9355ed63`) — the messy middle
- Raw build-in-public: Pi cluster experiments, Cloudflare tricks, the
  automation pipeline, failures, real costs. **Do NOT polish into corporate
  voice** — the messy middle is the point.
- Goal: grow 66 → **500 followers** for FB Stars (1/3 criteria met; Greece
  eligible). Funnel-friendly, followable, authentic — this is the
  monetization-critical surface.
- Content monetization beta: already on waitlist — nothing to do.

### Canonical page (`ad83c946`)
- Brand voice, product-funnel CTA → `https://social.cloudless.gr/pricing`.
- Don't break Messenger — the bot subscription lives on this page.

## Publishing paths

- **Personal profile: sidecar ONLY.** `_publish_facebook` routes
  `account_type=user` + browser session → `_publish_facebook_via_sidecar`.
  Meta removed user-profile posting from Graph (v19+) — a Graph attempt only
  yields the misleading `#200` error, so the code deliberately does NOT fall
  through. Sidecar endpoints: `/post/text`, `/post/photo`, `/post/link`.
- **Page: Graph API** with the stored page token (`_facebook_page_token`
  dynamic-lookup fallback). Text/link via `FacebookAPIClient`; photo albums
  via unpublished-upload flow.
- Groups: `account_type=group` → soft-skip (Groups API removed in v19+).

## Media rules (`_PLATFORM_MEDIA_RULES.facebook`)

- Optional media; jpg/png/gif + video; 200–4096px; ≤10MB image, ≤2GB video;
  ≤10 items. No PDF.

## Stars status check (facebook-browser-sidecar)

Navigate `https://www.facebook.com/professional_dashboard/monetization/stars/`
then `/debug/page-text`. Criteria: country ✅ · 500 followers ❌ · held 30d ❌.

## Gotchas

- Session death → `session-auto-heal` (cookie re-inject / credential login).
- `#200` error on the page = token/permission issue → `publish-alert-triage`.
- `facebook-engagement` skill for engagement-loop work.
