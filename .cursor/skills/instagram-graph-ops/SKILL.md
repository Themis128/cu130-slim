---
name: instagram-graph-ops
description: Instagram Graph API ops tooling — publish quota, insights metric probing, follower/engaged demographics, hashtag search + top media, business_discovery competitor lookups via scripts/instagram_tool.py. Use for IG account health, quota checks, audience analytics, hashtag research, or debugging IGApiException capability errors.
---

# Instagram Graph Ops

Ops surface over `InstagramAPIClient` (`app/services/instagram_api.py`) — the
same pattern as `meta-graph-ops` / `linkedin-graph-ops` / `viber-ops`.
Reads accounts + decrypts tokens from the DB; never prints tokens.

## Tool: `social-automation/backend/scripts/instagram_tool.py`

```bash
docker cp social-automation/backend/scripts/instagram_tool.py social-api:/app/scripts/
docker exec social-api python3 /app/scripts/instagram_tool.py accounts
docker exec social-api python3 /app/scripts/instagram_tool.py me
docker exec social-api python3 /app/scripts/instagram_tool.py quota
docker exec social-api python3 /app/scripts/instagram_tool.py insights [--metric reach]
docker exec social-api python3 /app/scripts/instagram_tool.py demographics --breakdown country
docker exec social-api python3 /app/scripts/instagram_tool.py engaged --breakdown age
docker exec social-api python3 /app/scripts/instagram_tool.py media --limit 15
docker exec social-api python3 /app/scripts/instagram_tool.py hashtag automation
docker exec social-api python3 /app/scripts/instagram_tool.py discover <username>
```

`/app/scripts` is not bind-mounted — `docker cp` first if stale.

## Client additions (v26)

- `search_hashtag(q)` / `get_hashtag_top_media` / `get_hashtag_recent_media`
- `business_discovery(username)` — public metrics + recent media of any
  Business/Creator account
- `get_follower_demographics(breakdown=age|country|city|gender)` and
  `get_engaged_audience_demographics(...)` — v22+ metric names; the old
  `audience_*` and `online_followers` metrics are REMOVED at v26
- `get_remaining_publish_quota` handles `quota_usage` as a bare int (v26
  shape) — previously only the list-of-metrics shape parsed, silently
  undercounting used quota

## Gotchas

- **`graph.instagram.com` (business_login) ≠ `graph.facebook.com`** —
  `ig_hashtag_search` and `business_discovery` exist ONLY on the FB Graph.
  The `@cloudless.gr` account is `login_type=business_login`, so both return
  `capability_unavailable` until the account is reconnected via the FB OAuth
  flow. The tool prints this note instead of crashing.
- Insights metrics at v26: `reach, follower_count, website_clicks,
  profile_views, online_followers, accounts_engaged, total_interactions,
  likes, comments, shares, saves, replies, *_demographics,
  follows_and_unfollows, profile_links_taps, views, threads_*` — `impressions`
  is gone (the tool's default probe flags it).
- Demographics endpoints return `{"data": []}` below ~100 followers — empty
  is valid, not an error.
- `quota_usage` int vs list shape: `get_remaining_publish_quota` handles both;
  remaining = `quota_total (25) - used`.

## Adoption notes

- `subzeroid/instagrapi` already in the stack via `instagrapi_client.py`
  (private-API fallback path) — unchanged.
- `ldtsystem2020/Instagram_API` and friends were evaluated but not adopted —
  unofficial private-API wrappers duplicating what instagrapi + the
  instagram-private-api sidecar already cover, with more ToS risk.
- `tdlib`-style official infra: none exists for IG — the official surface is
  the Graph API we already use.
