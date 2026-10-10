---
name: tiktok-api-ops
description: TikTok Open API ops tooling via scripts/tiktok_tool.py — token validation, creator_info publish prerequisites (privacy options, max duration, duet/stitch/comment toggles), video list with counts, publish-status checks, DM conversations. Use for TikTok token health, publish diagnostics, or checking which scopes a token can reach. Console/domain work stays in tiktok-console-ops.
---

# TikTok API Ops

Ops surface over `TikTokAPIClient` (`app/services/tiktok_api.py`) — the same
pattern as `meta-graph-ops` / `linkedin-graph-ops` / `instagram-graph-ops`.
Reads accounts + decrypts tokens from the DB; never prints tokens.

## Tool: `social-automation/backend/scripts/tiktok_tool.py`

```bash
docker cp social-automation/backend/scripts/tiktok_tool.py social-api:/app/scripts/
docker exec social-api python3 /app/scripts/tiktok_tool.py accounts
docker exec social-api python3 /app/scripts/tiktok_tool.py validate
docker exec social-api python3 /app/scripts/tiktok_tool.py creator
docker exec social-api python3 /app/scripts/tiktok_tool.py videos --limit 10
docker exec social-api python3 /app/scripts/tiktok_tool.py status <publish_id>
docker exec social-api python3 /app/scripts/tiktok_tool.py dms
```

`/app/scripts` is not bind-mounted — `docker cp` first if stale.
`-a/--account <uuid-prefix>` selects a specific account.

## Verified live (2026-10, @cloudless.gr)

- `validate` → token active; user.info scopes return
  open_id/union_id/display_name/avatar
- `creator` → publish prerequisites live: `max_video_post_duration_sec: 3600`,
  comment/duet/stitch all enabled, privacy options listed — use this BEFORE
  publishing (Direct Post requires querying it first per TikTok UX rules)
- `videos` → Display API returns real metrics (views/likes/comments)
- Token scopes on the account: `user.info.basic user.info.profile
  user.info.stats video.list video.publish video.upload`

## Coverage notes (what's already in the client)

`app/services/tiktok_api.py` already covers: `init_video_post` (both
`PULL_FROM_URL` and `FILE_UPLOAD` with chunked upload + probing),
`init_photo_post` (+ media upload), `check_publish_status`,
`cancel_publish`, `list_videos`/`query_video`, DM list/read/send. Nothing
additional was needed — this tool is the ops entry point.

## Skipped repos (github.com tiktok search)

- `tiktok/tiktok-opensdk-ios` / `tiktok-opensdk-android` — mobile Login/Share
  kits only; not applicable to a server backend
- Unofficial TikTok scrapers/wrappers — the browser sidecar (port 9224) +
  console MCP already cover non-API paths with better fidelity
- Content Posting API has no official server SDK — the in-tree httpx client
  matches the documented v2 surface directly

Related: `tiktok-console-ops` (dev console, domain verification, app audit),
`tiktok-content-ops` (what to publish, DIRECT_POST rules).
