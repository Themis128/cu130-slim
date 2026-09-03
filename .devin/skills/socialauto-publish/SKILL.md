---
name: socialauto-publish
description: >-
  Create, schedule, publish, and manage social posts through the SocialAuto
  API. Use when creating posts for LinkedIn/Twitter/Facebook/Instagram/Threads/TikTok,
  scheduling posts, publishing drafts now, duplicating posts, listing posts,
  or checking post status. Covers the /api/v1/content/* endpoints.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# SocialAuto Publish

Create, schedule, and publish social posts through the SocialAuto backend API.

## When to use

- Create a post (text, media, link) for one or more social platforms
- Schedule a post for later publishing
- Publish a draft post immediately
- Duplicate an existing post as a new draft
- List or inspect posts and their publish status
- Delete a post

## API base

```
http://127.0.0.1:8083/api/v1/content
```

## Authentication

All endpoints require a Bearer token from `POST /api/v1/auth/login`
(form-encoded `username` + `password`). The helper script handles login
automatically by reading `.env` for `SOCIAL_ADMIN_EMAIL` / `SOCIAL_ADMIN_PASSWORD`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/posts` | Create a post (draft or scheduled) |
| GET | `/posts` | List posts (filter by status, platform, search) |
| GET | `/posts/{id}` | Get a single post |
| PUT | `/posts/{id}` | Update a post |
| DELETE | `/posts/{id}` | Delete a post |
| POST | `/posts/{id}/schedule` | Schedule a post for a future time |
| POST | `/posts/{id}/publish-now` | Publish a post immediately |
| POST | `/posts/{id}/duplicate` | Duplicate a post as a new draft |
| GET | `/posts/calendar` | Calendar view of scheduled posts |

## Post creation body

```json
{
  "content_text": "Your post text with #hashtags",
  "media_ids": ["uuid-of-media-asset"],
  "hashtags": ["serverless", "cloud"],
  "link_url": "https://www.cloudless.gr",
  "target_account_ids": ["uuid-of-social-account"],
  "scheduled_at": "2026-09-01T10:00:00Z",
  "status": "draft"
}
```

- `media_ids`: optional, references media library assets
- `target_account_ids`: **required for publishing** — list of social account UUIDs to publish to. Use `GET /api/v1/accounts` to find account IDs. If omitted, the post will have no targets and `publish-now` will return a 400 error.
- `scheduled_at`: ISO 8601, omit for draft
- `status`: `draft` or `scheduled`

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Publish a draft post immediately
.devin/skills/socialauto-publish/scripts/publish-post.sh <post-id>

# Schedule a post for a specific time
.devin/skills/socialauto-publish/scripts/schedule-post.sh <post-id> "2026-09-01T10:00:00Z"

# List recent posts
.devin/skills/socialauto-publish/scripts/list-posts.sh [--status draft|scheduled|published|failed] [--limit 10]

# Create a quick text-only post
.devin/skills/socialauto-publish/scripts/create-post.sh "Your post text" [--platform linkedin] [--schedule "2026-09-01T10:00:00Z"]

# Delete a post
.devin/skills/socialauto-publish/scripts/delete-post.sh <post-id>
```

## Post status flow

```
draft → scheduled → publishing → published
                                  ↘ failed
```

## Platform support

Posts can target any combination of connected accounts:
- LinkedIn (person or organization/Company Page)
- Twitter / X
- Facebook (user or page)
- Instagram (business)
- Threads
- TikTok

## Important notes

- The `publish-now` endpoint queues the post via Celery; publishing happens
  asynchronously. Check status after a few seconds.
- LinkedIn carousel posts use the Documents API (PDF upload).
- TikTok requires video media for direct publishing.
- Instagram requires a media asset (image or video) for publishing.
- Never print or commit `.env` secrets.
