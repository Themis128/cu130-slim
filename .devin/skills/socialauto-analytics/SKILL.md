---
name: socialauto-analytics
description: >-
  View social media analytics and post performance in SocialAuto. Covers
  /api/v1/analytics/* endpoints. Use when checking post performance, platform
  analytics (LinkedIn impressions, Twitter engagement, etc.), account-level
  stats, or exporting analytics reports.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# SocialAuto Analytics

View social media performance metrics and analytics.

## When to use

- Get post-level analytics (impressions, clicks, engagement)
- Get account-level stats (followers, growth, reach)
- View platform-specific analytics (LinkedIn, Twitter, Facebook, etc.)
- Export analytics as CSV/JSON
- Check publishing queue status

## API base

```
http://127.0.0.1:8083/api/v1/analytics
```

## Authentication

Bearer token from `POST /api/v1/auth/login`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/posts` | Post-level analytics across platforms |
| GET | `/posts/{id}` | Analytics for a specific post |
| GET | `/accounts/{id}` | Account-level stats |
| GET | `/accounts/{id}/platform` | Platform-specific detailed stats |
| GET | `/summary` | Cross-platform summary |
| GET | `/export` | Export analytics as CSV/JSON |
| GET | `/linkedin/organizations/{id}` | LinkedIn organization analytics |

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Get analytics summary
.devin/skills/socialauto-analytics/scripts/analytics-summary.sh

# Get post analytics
.devin/skills/socialauto-analytics/scripts/post-analytics.sh <post-id>

# Get account analytics
.devin/skills/socialauto-analytics/scripts/account-analytics.sh <account-id>

# Export analytics
.devin/skills/socialauto-analytics/scripts/export-analytics.sh [--format csv|json]
```

## Metrics by platform

| Platform | Available metrics |
|----------|------------------|
| LinkedIn | Impressions, clicks, likes, comments, shares, engagement rate |
| Twitter/X | Impressions, retweets, replies, likes, profile clicks, engagement rate |
| Facebook | Reach, impressions, likes, comments, shares, click-through rate |
| Instagram | Reach, impressions, likes, comments, saves, profile views |
| Threads | Replies, reposts, likes |
| TikTok | Views, likes, comments, shares, reach |

## Important notes

- Analytics are fetched on-demand from platform APIs and cached in
  `post_analytics_snapshots` table.
- LinkedIn organization analytics require the organization URN.
- Some platforms may not return real-time data; snapshots are taken
  periodically by a Celery beat task.
- Export supports CSV and JSON formats.
