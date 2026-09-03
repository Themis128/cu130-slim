---
name: tiktok-publish
description: >-
  Publish videos and photo posts to TikTok through SocialAuto. Covers
  FILE_UPLOAD (no domain verification needed), MEDIA_UPLOAD vs DIRECT_POST
  modes, spam protection limits, publish ID polling, and the TikTok Content
  Posting API client. Use when posting to TikTok, debugging TikTok publish
  failures, checking pending share limits, or cancelling stuck uploads.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# TikTok Publish

Publish video and photo content to TikTok through the SocialAuto backend.

## When to use

- Create and publish a TikTok video post (FILE_UPLOAD or PULL_FROM_URL)
- Create and publish a TikTok photo carousel post (PULL_FROM_URL only)
- Debug TikTok publish failures (spam limits, domain verification, token issues)
- Cancel stuck pending TikTok uploads
- Check TikTok publish status and poll for completion
- Build slideshow videos from images for TikTok

## TikTok account

| Field | Value |
|-------|-------|
| Platform | tiktok |
| Display name | cloudless.gr |
| Account type | person |
| Sandbox user | cloudless-dev (target: user3113682023385) |
| Token lifetime | 24 hours (refreshed daily by beat task) |
| Client key env | `TIKTOK_CLIENT_KEY` |
| Redirect URI | `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback` (HTTPS required) |

Find the current account ID with:
```bash
.devin/skills/socialauto-accounts/scripts/list-accounts.sh | grep tiktok
```

## Publish modes

| Mode | Description | Requires audit? |
|------|-------------|----------------|
| `MEDIA_UPLOAD` | Sends video to TikTok inbox for creator to post manually | No |
| `DIRECT_POST` | Posts directly to the creator's profile | Yes — app must be audited |

Until the app passes TikTok's audit, **always use `MEDIA_UPLOAD`**.
`DIRECT_POST` returns `unaudited_client_can_only_post_to_private_accounts` for
unaudited apps. Even with `SELF_ONLY` privacy, unaudited apps are blocked.

## Media transfer methods

| Method | Video | Photo | Domain verification? |
|--------|-------|-------|---------------------|
| `FILE_UPLOAD` | Yes (read local file, upload bytes) | No | Not needed |
| `PULL_FROM_URL` | Yes (TikTok downloads from URL) | Yes (only method) | Required |

### FILE_UPLOAD (preferred for videos)

SocialAuto's `_publish_tiktok` in `app/services/publishing.py` automatically
uses `FILE_UPLOAD` when a local video file is available (`.mp4`, `.mov`,
`.webm`). This bypasses the domain verification requirement entirely.

The flow:
1. `init_video_upload(source=FILE_UPLOAD, video_size=N)` → returns `upload_url`
2. `upload_video_file(upload_url, video_bytes)` → PUT chunks to TikTok
3. Poll `check_publish_status(publish_id)` until `SEND_TO_USER_INBOX`

### PULL_FROM_URL (required for photos)

Photo posts only support `PULL_FROM_URL`. The domain serving the images must
be verified in the TikTok developer console. See the `tiktok-dev-console` skill
for domain verification instructions.

## Spam protection: 5 pending shares per 24h

TikTok limits API uploads to **5 pending shares within any 24-hour period**.
Each `MEDIA_UPLOAD` init creates a pending share in the creator's TikTok inbox.
If the creator doesn't post or discard them, the limit is hit.

Error: `spam_risk_too_many_pending_share`

### Clearing pending shares

1. **From the TikTok mobile app**: Open TikTok → Inbox/Drafts → post or delete each pending upload.
2. **Via the cancel API**: Use the cancel script with a known publish_id:
   ```bash
   .devin/skills/tiktok-publish/scripts/cancel-upload.sh <publish_id>
   ```
3. **Wait 24 hours**: Pending shares expire automatically after 24h.

### Preventing spam lockout

- Always delete failed SocialAuto posts after debugging (prevents beat task retries).
- Check pending count before bulk publishing:
  ```bash
  .devin/skills/tiktok-publish/scripts/check-pending.sh
  ```
- The Celery beat task `check_scheduled_posts` will re-process scheduled posts
  every 30s — if a post is stuck in `scheduled` status, it keeps retrying and
  flooding TikTok's inbox. Delete or mark failed posts to stop this.

## Publish ID format

TikTok FILE_UPLOAD publish IDs use the format `v_inbox_file~v2.<numeric_id>`,
which includes `~` and `.` characters. The `_ID_RE` regex in
`app/services/tiktok_api.py` accepts these: `^[a-zA-Z0-9_\-~.]+$`.

PULL_FROM_URL publish IDs are simpler alphanumeric strings.

## Upload URL hosts

TikTok returns regional upload hosts (e.g. `open-upload-i18n.tiktokapis.com`,
`open-upload.tiktokapis.com`). The `upload_video_file` method accepts any
`*.tiktokapis.com` host.

## Creating a TikTok post via SocialAuto API

```bash
# Create a video post (FILE_UPLOAD, MEDIA_UPLOAD mode)
source .env
API="http://127.0.0.1:8083"
TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$SOCIAL_ADMIN_EMAIL" \
  --data-urlencode "password=$SOCIAL_ADMIN_PASSWORD" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -sf -X POST "$API/api/v1/content/posts" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "content_text": "Your caption with #hashtags",
    "media_ids": ["<video-asset-id>"],
    "target_account_ids": ["<tiktok-account-id>"],
    "platform_specific": {
      "tiktok": {
        "publish_mode": "MEDIA_UPLOAD"
      }
    },
    "status": "draft"
  }'

# Then publish:
.devin/skills/socialauto-publish/scripts/publish-post.sh <post-id>
```

## Building slideshow videos from images

TikTok requires video for FILE_UPLOAD. To convert carousel slides into a
slideshow video, use ffmpeg in the `comfyui` container (the only container
with ffmpeg installed):

```bash
# 1. Download slides to comfyui's input mount
INPUT="/home/tbaltzakis/cu130-slim/storage-user/input/tiktok-slides"
mkdir -p "$INPUT"
curl -s -o "$INPUT/slide-1.png" "https://social.cloudless.gr/api/v1/media/view?path=<storage_path>"

# 2. Build slideshow (3s per slide, 1080x1080 square for TikTok)
docker exec social-media-comfyui-gpu bash -c '
cd /home/user/ComfyUI/input/tiktok-slides
printf "file '\''%s'\''\nduration 3\n" slide-{1..5}.png > /tmp/slideshow.txt
# Repeat last frame (concat demuxer requirement)
echo "file '\''slide-5.png'\''" >> /tmp/slideshow.txt
ffmpeg -y -f concat -safe 0 -i /tmp/slideshow.txt \
  -vf "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p" \
  -r 30 -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
  /home/user/ComfyUI/output/cloudless-tiktok-slideshow.mp4
'

# 3. Copy to host and upload to media library
cp storage-user/output/cloudless-tiktok-slideshow.mp4 /tmp/
# Then upload via /api/v1/media/upload
```

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Cancel a pending TikTok upload by publish_id
.devin/skills/tiktok-publish/scripts/cancel-upload.sh <publish_id>

# Check how many pending shares exist (queries TikTok status for known IDs)
.devin/skills/tiktok-publish/scripts/check-pending.sh

# Poll a post's publish status until complete or failed
.devin/skills/tiktok-publish/scripts/poll-status.sh <post-id>

# Build a slideshow video from image assets in the media library
.devin/skills/tiktok-publish/scripts/build-slideshow.sh <asset-id-1> [<asset-id-2> ...]
```

## Common errors and fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `url_ownership_unverified` | Domain not verified for PULL_FROM_URL | Verify domain in TikTok dev console, or use FILE_UPLOAD for videos |
| `spam_risk_too_many_pending_share` | 5+ pending uploads in 24h | Clear pending from TikTok app, cancel via API, or wait 24h |
| `unaudited_client_can_only_post_to_private_accounts` | DIRECT_POST without app audit | Use MEDIA_UPLOAD mode instead |
| `Invalid publish_id format` | Regex rejected `~` or `.` in publish_id | Fixed — regex now accepts `~` and `.` |
| `upload_url must use the TikTok upload host` | Host validation too strict | Fixed — accepts any `*.tiktokapis.com` host |
| `access_token_invalid` | 24h token expired | Refresh token or reconnect account |

## Code references

- `app/services/publishing.py` — `_publish_tiktok()` function (FILE_UPLOAD + PULL_FROM_URL)
- `app/services/tiktok_api.py` — `TikTokAPIClient` class (init, upload, status, cancel)
- `app/api/auth.py` — `TikTokOAuth2` class (client_key, PKCE, comma-separated scopes)
- `app/worker/tasks/token_refresh.py` — `refresh_expiring_tokens` beat task
