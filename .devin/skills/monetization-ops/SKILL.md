---
name: monetization-ops
description: >
  Monetization-readiness tracking — app/services/monetization.py holds every
  platform's creator-program thresholds (FB Stars 500-followers/30-day hold,
  TikTok Creator Rewards 10k+100k views, X Ads Revenue Share 500+Premium+5M
  impressions, IG Subscriptions ~10k invite band) and
  scripts/monetization_tool.py evaluates each connected account live against
  them. Use for FB-Stars progress, monetization eligibility checks, follower
  growth rate questions, or "when do we hit the threshold" estimates.
---

# Monetization ops

Monetization is the primary optimization goal — this skill turns every
platform's monetization gate into a measurable report instead of
hand-checking consoles.

## Tool

```bash
docker exec social-api python3 /app/scripts/monetization_tool.py <cmd>
```

- `report` — every active SocialAccount evaluated against its program:
  criteria ✓/✗/? (manual), live followers vs snapshot, trend, days-to-goal.
- `growth` — follower-snapshot trend per account (30d, net/day).

Metrics sources: live platform calls (IG `get_profile` followers_count,
TikTok `user.info stats` + `video.list` views sum, X `users/me` metrics),
`follower_snapshots` for everything else and for the FB-Stars 30-day-hold
computation. Failures degrade to snapshots; `?` = criterion only the
platform UI can answer (Premium state, invites, standing).

## Program truth table (in `PLATFORM_PROGRAMS`)

| Platform | Program | API-checkable gates | Manual gates |
|---|---|---|---|
| facebook (user/professional) | FB Stars | followers ≥500, held 30d | country, standing, CM invite (waitlisted) |
| facebook (page) | Content Monetization / Subscriptions | followers ≥10k band | invite |
| tiktok | Creator Rewards | 10k followers, 100k views/30d | country |
| twitter | Ads Revenue Share | followers ≥500 | Premium active, 5M imp/90d (pay-per-use endpoint) |
| instagram | Subscriptions/Gifts | 10k band | invite |
| linkedin/threads/bluesky + messaging | — | — | funnel surfaces → Polar/audit CTA |

**Meta program consolidation (verified 2026-10):** In-stream ads, Ads on
Reels and the Performance bonus **ended 2025-08-31** — folded into the
unified, invite-only "Facebook Content Monetization" program (no published
numeric threshold; the interest form in Professional dashboard →
Monetization is a waitlist, not an application — already on it).
**Stars remains a standalone program** (500 followers held 30d). Meta also
runs **Creator Fast Track** (2026-03): ≥100k followers on IG/TikTok/YouTube
→ $1k–$3k/mo ×3mo + instant CM access — US/CA/UK/AU only, not actionable
for us yet.

## Verified baseline (2026-10)

- FB profile @themis.baltzakis: **66/500 followers, +0.1/day** — the
  Stars blocker is growth, not setup.
- TikTok @cloudless.gr: 4 followers, 2,242 views/30d (needs 10k/100k).
- X @TBaltzakis: 0 followers; Premium state manual.
- IG @cloudless.gr: 12 followers (+0.5/day).
- LinkedIn org: 53 followers (+2.5/day) — funnel only, fastest-growing.

## Notes

- Thresholds change — verify against the current program docs before
  acting on a "met" result near a boundary.
- `days_held` needs daily follower snapshots; gaps in the snapshot series
  undercount the streak.
- Repo-search finding: no monetization library was adoptable — sponsor
  dashboards and crypto streamers are irrelevant; this tracker is the
  useful artifact.
