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

## Multi-platform publish recipe (media-correct)

Each platform needs the right media type — don't attach a video to
image-only targets or vice versa. The proven recipe:

1. **Image post** — `media_ids=[image_asset]` → targets: Facebook Page,
   Instagram Business, LinkedIn org, Threads, Twitter/X.
2. **Video post** — separate post with `media_ids=[video_asset]` →
   target: TikTok only (video required for FILE_UPLOAD/DIRECT_POST).
3. **Telegram/WhatsApp** — never target for feed posts; they are
   messaging channels and the worker marks them `skipped` by design.
4. Verify with `scripts/publish_queue.py targets <post_id>` — every
   target should reach `published` with a `platform_post_id`/`url`.

Via the `socialauto` MCP server: `create_post` accepts `platforms`
(names) or `account_ids` (UUIDs) — it resolves them to
`target_account_ids`. MCP `list_accounts` + `list_media` give the IDs.

## Post-publish verification

After `publish-now`, confirm the media actually landed — not just the
status flag:

- **Facebook**: `GET /{page-id}/feed` → post has `attachments` with
  `media_type: photo`/`video`.
- **Instagram**: `GET /{ig-media-id}?fields=media_type,media_url` →
  `IMAGE`/`VIDEO` with CDN URL.
- **Threads**: target `platform_url` resolves; `media_type` on the post.
- **LinkedIn**: worker log shows `POST /rest/posts` → 201 + prior asset
  upload `201` (`/rest/images` or `/rest/videos`).
- **TikTok**: `SEND_TO_USER_INBOX` means FILE_UPLOAD mode — video sits in
  the TikTok inbox for manual publish until DIRECT_POST audit passes.
- **X/Twitter**: check `x.com/<handle>` latest article — text + image
  present. API `402 credits depleted` routes to the browser fallback
  (see `twitter-browser-ops` skill).

## Important notes

- The `publish-now` endpoint queues the post via Celery; publishing happens
  asynchronously. Check status after a few seconds.
- **Publishing-time spellcheck**: `publish_to_platform` in `app/services/publishing.py`
  spellchecks the final assembled post text (including platform-specific overrides,
  hashtags, and link URLs) via LanguageTool `auto_correct` before dispatching. This
  is advisory — spellcheck failures never block publishing.
- **Instagram link handling**: Instagram captions do **not** include `link_url`
  (Instagram has no clickable caption links, and SEO scoring penalizes links in
  IG captions). The `link_url` field is still stored on the post but is omitted
  from the rendered Instagram caption.
- **Instagram token resolution**: `_resolve_ig_user_token` in `publishing.py`
  resolves the correct Facebook **user** token (not Page token) for Instagram
  Graph API publishing by looking up the parent Facebook user account.
- LinkedIn carousel posts use the Documents API (PDF upload).
- TikTok requires video media for direct publishing.
- Instagram requires a media asset (image or video) for publishing.
- Never print or commit `.env` secrets.

## Quality pipeline

All AI content generation endpoints (`/api/v1/ai/generate-content`,
`/api/v1/ai/improve-content`, `/api/v1/ai/generate-carousel`,
`/api/v1/ai/generate-carousel-pipeline`, `/api/v1/ai/run-carousel-and-publish`,
`/api/v1/ai/analyze-content`) enforce a three-step quality pipeline:

1. **Spellcheck** — LanguageTool grammar + spelling correction
2. **NLP** — plain-English check/fix (jargon detection + rewrite)
3. **SEO** — platform-specific scoring (length, hashtags, readability, keywords, links)

If the SEO score is below 90, the pipeline auto-improves the content by feeding
recommendations back to the LLM and regenerating (up to 2 iterations).

See `app/services/quality_pipeline.py` for the shared helper and
`AGENTS.md` → "Quality pipeline" section for full documentation.
