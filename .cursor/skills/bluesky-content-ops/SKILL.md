---
name: bluesky-content-ops
description: >-
  What to publish on Cloudless Bluesky (@tbaltzakis.bsky.social) —
  conversational AT Protocol text posts, threads, and image posts via the
  app-password session. Covers the chronological Following feed, custom-feed
  keyword matching, and the developer-voice positioning. Use for any Bluesky
  post.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Bluesky Content Ops

Read `social-content-core` first.

## Surface

- Account `3134326c-2481-4f29-91f8-71bc4b817a0d` · `@tbaltzakis.bsky.social`
  · DID `did:plc:isl4yqjzxu3s7qwah24cb5xv`.
- Auth is app-password (no OAuth) — see `bluesky-ops` for connect/status/
  rotate tooling (`scripts/bluesky_tool.py`).

## Content rules

<!-- PLATFORM-FIT:BEGIN bluesky -->
### What the Bluesky audience/algorithm favors
- No single hidden algorithm: the Following feed is reverse-chronological, Discover is Bluesky's own recommender, and 50k+ custom feeds rank by their own topic rules — earning a spot in the feeds your audience pins matters more than follower count.
- Write posts that stand alone (feed browsers lack context), use specific topic keywords/hashtags for feed matching, and post consistently — feed refresh cycles surface recent posts, not archives.
- Cadence (Buffer 2026, 3M+ posts): consistent posting through the day beats bursts; weekends are the peak (Saturday ~5pm top slot, 4–7pm best window, weekday evenings 6–9pm next best).

<!-- PLATFORM-FIT:END -->

- Conversational, developer-voice posts — build logs, cloud cost wins,
  self-hosting notes, hot takes. More casual than LinkedIn, technical
  audience.
- ~300 grapheme practical limit — keep posts tight; use threads (reply
  chains) for depth.
- Every post must stand alone — feed readers see it without your previous
  posts as context.
- 1–3 specific topic keywords or hashtags per post (custom feeds match on
  them); no mega-tags.
- Reply/quote-post promptly after publishing — early engagement decides
  whether the post travels in custom feeds.

## Publishing path

- `bluesky_api.py` client: `createSession` (app password) → `uploadBlob` →
  `createRecord` with richtext facets. Wired into `_publish_bluesky` in
  `app/services/publishing.py`.
- Ops CLI: `scripts/bluesky_tool.py` — `accounts`, `status`, `facets`,
  `rotate` (credential rotation sweep exists as
  `app.worker.tasks.bluesky_credential`).
- Known failure mode: PDS `429 RateLimitExceeded` on session creation
  under repeated retries — back off rather than hammering.
