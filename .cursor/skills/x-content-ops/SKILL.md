---
name: x-content-ops
description: >-
  What to publish on X/Twitter (@TBaltzakis) — developer-voice personal
  content with real engineering numbers, the 280-weighted-char limit (URLs
  count 23 via t.co), the API → browser fallback publish chain, and the Polar
  pricing funnel CTA. Use for any tweet, thread, or X publish failure
  (quota, browser fallback selectors).
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# X (Twitter) Content Ops

Read `social-content-core` first.

## Surface

- Account `a89d6852-eff8-479a-835f-50d806cf59dd` · `@TBaltzakis` — personal dev voice.

## Content rules

- **Developer value angle**: raw code improvements, p95 latencies,
  implementation wins — real numbers only, never invented metrics.
- Personal account → personal funnel: can soft-point to
  `facebook.com/themis.baltzakis` when the build-log angle fits (≤1 in 3),
  otherwise product CTA.
- **Product CTA → `https://social.cloudless.gr/pricing`** (Polar). The
  profile website link already routes there via t.co.
- Threads/tweetstorms: hook → numbered build steps → result + CTA last tweet.
- Plain-English NLP check still applies — dev voice ≠ jargon soup.

## Limits

- **280 weighted chars** — `_fit_x_limit` trims; URLs count as 23 via t.co
  regardless of length.
- ≤4 media items; jpg/png/webp/gif + video; 200–8192px; ≤5MB image,
  ≤8GB video.
- Hashtags: 1–3 max — X punishes hashtag-stuffed posts.

## Publishing paths (`_publish_twitter` → `_publish_twitter_fallbacks`)

1. **Official API** (OAuth) — primary.
2. **Browser fallback** (`_publish_twitter_via_browser`) — when the API
   quota is exhausted or the token is revoked; uses the browser-bridge
   session (`browser-bridge-ops` skill for selectors).

## Gotchas

- Quota/rate-limit errors → the browser fallback usually picks them up; if
  it also fails check `publish-alert-triage` for the X selector drift table.
- API quota is the free tier — don't schedule high-frequency posting;
  batch into threads instead.
