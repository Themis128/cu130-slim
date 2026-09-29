# Facebook follower growth — Stars eligibility plan

Goal: grow `facebook.com/themis.baltzakis` (pro mode) and the Cloudless.gr
Pages to **500 followers held for 30 consecutive days** — the Stars +
monetization gate. Verified live Sep 29, 2026: profile at ~1 follower,
Pages at 0–1, **zero monetization violations** on all three, business
portfolio `cloudless.gr` (1558125105019725) already exists.

## What 2026 research says actually moves the needle

1. **Reach is per-post, not per-account.** Follower count is NOT a
   ranking signal. Half the feed is now recommended ("unconnected")
   content — small accounts can break out on a single strong Reel.
   Optimize each post for distribution, then convert engagers to followers.
2. **Signal weights (roughly)**: Reel watch time > comment threads >
   shares > first-hour engagement speed > reactions/saves.
3. **Reels are the discovery surface.** Every uploaded video is treated
   as a Reel. Meta explicitly demotes reposted/low-effort video and
   prefers original creator-made content.
4. **New indexing surfaces**: public pro-mode posts are indexed by
   Google (since Jul 2025) and feed Facebook's AI Mode answers (Jun 2026).
   Captions should contain quotable specifics — real numbers, € prices,
   steps, answers — not vague slogans.
5. **Engagement is two-way**: reply fast to every comment/DM, ask
   open-ended questions. First-hour engagement decides distribution.

## The five levers (ranked by effort-to-impact)

### 1. Friend invites — manual, biggest single jump
Meta officially allows inviting friends to follow (up to ~200 friends
per Page; pro-mode profiles have the same invite surface). With ~500
needed, a realistic 10–20% accept rate on an existing friend network
closes a large share of the gap in one afternoon.
**Owner: user** — personal friend list judgment; not automatable
safely (bulk-invite automation trips spam systems).

### 2. Reels cadence — automated (this stack)
3 short vertical Reels/week on the profile. Every Reel: hook inside the
first second, one concrete fact (real cost/config/metric), captions on.
Built via the existing pipeline: DMR copy → FLUX image frames →
ffmpeg 1080×1920 → SocialAuto media library → scheduled.

### 3. Daily feed posts — automated (running)
Text + photo posts keep the profile alive and feed search indexing.
Cadence already running Sep 29–Oct 3; extended by the next wave below.
Google/AI-Mode-friendly copy: concrete claims, real numbers.

### 4. Convert engagers — semi-manual
When posts get reactions from non-followers, Facebook offers "Invite to
follow" next to their name on the post's reaction list. ~2 min/week.
Also: reply to every comment within the first hour (engagement velocity).

### 5. Cross-promotion — automated (running)
4-post drip Oct 1–15 (LinkedIn ×2, Threads, Instagram) pointing at the
profile. X/Twitter stays opportunistic/manual (last-tier rule).

## Cadence targets

| Surface | Frequency | Function |
|---|---|---|
| Reels | 3×/week | discovery — new audience |
| Feed posts (text/photo) | 4–5×/week | trust + search indexing |
| Stories | 3–5×/week | retention (manual or n8n) |
| Comment replies | within 1h of posting | engagement velocity |
| Friend/reactor invites | weekly sweep | direct conversion |

## What NOT to do

- No fake followers / engagement pods — monetization review checks
  authentic engagement and Partner Monetization Policies.
- No reposted/TikTok-watermarked videos — originality is scored.
- No same-day identical posts across platforms — reads as spam.
- No new scheduled content into X/TikTok (last-tier rule).
- No ad spend — the Page's "Boost unavailable"/"Create ad account"
  items require a payment method; skipped by standing rule.

## Timeline to eligibility

- 500 followers is the hard gate. At current content velocity + invites,
  realistic window is 4–8 weeks; a breakout Reel can collapse it to days.
- Then 30 consecutive days ≥500 → earliest Stars unlock ≈ late Nov 2026.
- Content Monetization invite is independent — already waitlisted.
- Payout/tax onboarding when invited: dashboard-only, user fills
  payment details (never automated).

## Instrumentation

- `GET /api/v1/analytics/followers` — track profile + Page series weekly.
- Professional dashboard → Progression + weekly task counters.
- Dashboard's own weekly tasks (posts/photos/reels/comments/followers)
  double as the growth checklist — keep them all "Completed" weekly.
