---
name: tiktok-console-ops
description: >-
  TikTok for Developers console + SocialAuto TikTok runtime: domain verification (PULL_FROM_URL), DNS TXT via Cloudflare, sidecar session, app audit, config drift, and the DIRECT_POST/MEDIA_UPLOAD publish path. Use for TikTok console, url_ownership_unverified, domain verify, or publish errors.
---

# Tiktok Console Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| TikTok console ops | `tiktok-console-ops` |
| TikTok Publish | `tiktok-console-ops` → `tiktok-publish/` |

## TikTok console ops

Official docs (Context7 `/websites/developers_tiktok` or developers.tiktok.com):

- Content Posting get-started / media transfer — `PULL_FROM_URL` needs verified domain
- Photo posts require `PULL_FROM_URL` (domain verify mandatory)
- Videos can use `FILE_UPLOAD` (no domain verify) or `PULL_FROM_URL`
- `DIRECT_POST` pending — app approved 2026-09-29, but the separate Direct Post audit is **under review** (submitted 2026-09-29 via `/application/content-posting-api`). Until it clears, init returns `unaudited_client_can_only_post_to_private_accounts` and code auto-falls back to `MEDIA_UPLOAD`; per-post override stays available (`platform_specific.tiktok.publish_mode`)
- Login Kit: `client_key`, PKCE S256, comma-separated scopes, HTTPS redirect only

### Cloudless defaults

| Field                    | Expected                                                         |
| ------------------------ | ---------------------------------------------------------------- |
| App name / ID            | Cloudless / `7630494700880906241`                                |
| Redirect URI             | `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback`  |
| Website URL (audit)      | `https://cloudless.gr` (public site — not `social.cloudless.gr`) |
| Web / media domain       | `cloudless.gr` (covers `social.cloudless.gr`)                    |
| Connected account        | sandbox `user3113682023385` / brand cloudless.gr                 |
| Publish mode (approved)  | `DIRECT_POST` + `PUBLIC_TO_EVERYONE` (defaults; auto-falls back to `MEDIA_UPLOAD` if the unaudited flag lingers; per-post overrides) |
| Sidecar                  | `http://127.0.0.1:9224`                                          |
| Domain verify status     | `cloudless.gr` already verified in Production URL properties     |

**Drift to fix if seen in console:** `social.cloudless.jp` web URL or redirect — replace with `.gr` SocialAuto paths above.

### Env (never print secrets)

- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`
- `TIKTOK_DEV_EMAIL`, `TIKTOK_DEV_PASSWORD` — developer portal login
- `CLOUDFLARE_API_TOKEN` — DNS TXT for `tiktok-domain-verification=…`
- Site TXT `tiktok-developers-site-verification=…` is **not** Content Posting domain verify
- `domain-verify.py` now short-circuits when `cloudless.gr` is already listed under **Verified properties**

### Tool scripts (repo root)

```bash
.cursor/skills/tiktok-console-ops/scripts/check-config.py
.cursor/skills/tiktok-console-ops/scripts/check-scopes.py          # granted vs expected OAuth scopes
.cursor/skills/tiktok-console-ops/scripts/cp-audit-application.py  # Content Posting (Direct Post) audit wizard; --submit to finalize
.cursor/skills/tiktok-console-ops/scripts/sidecar-session.py ensure   # Playwright Docker → POST /session
.cursor/skills/tiktok-console-ops/scripts/sidecar-session.py status
.cursor/skills/tiktok-console-ops/scripts/console-inspect.py          # login + dump app state
.devin/skills/tiktok-console-ops/scripts/domain-verify.py            # console token → CF TXT → Verify (or exits early if already verified)
.cursor/skills/tiktok-console-ops/scripts/dns-tiktok-txt.py list|add <token>
```

Audit-fix helpers (Playwright Docker, under `scripts/lib/`):

- `rejection-reason.mjs` — click **See why** and dump reviewer notes
- `draft-fix-website-url.mjs` / `upload-icon-submit.mjs` — return to draft, set Website URL to `https://cloudless.gr`, restore app icon, submit
- Prefer Terms/Privacy: `https://cloudless.gr/en/terms` and `https://cloudless.gr/en/privacy`

### Content Posting audit application (Direct Post gate)

Public `DIRECT_POST` needs a **separate audit** beyond app approval — the
"Apply" link beside the Direct Post toggle opens a 4-step wizard at
`/application/content-posting-api`. `cp-audit-application.py` fills it;
`--submit` finalizes (declaration checkboxes + Next). Verified submitted
2026-09-29 → console shows **Under review** beside Direct Post.

Wizard gotchas (script handles all):

- State is **not persisted** — each run starts at step 1.
- Daily-user estimate is a `button[aria-haspopup="listbox"]`, not an
  input; picking an option reveals a second required textarea.
- Step 3 needs an MP4 screen recording of OAuth→compose→post UX
  (`docs/tiktok-demo/videos/tiktok-demo.mp4`) + a DB-fields list.
- Review step needs 3 declaration checkboxes; the Next button spins a
  while during submit — wait for it.
- The submit lands asynchronously: re-open the app page and confirm
  "Under review" beside Direct Post.

### MCP server

`tiktok-console` in `.devin/mcp_config.json` →
`.cursor/skills/tiktok-console-ops/scripts/tiktok-console-mcp-server.py`

Tools: `tiktok_check_config`, `tiktok_sidecar_status`, `tiktok_sidecar_ensure_session`,
`tiktok_dns_list`, `tiktok_dns_add_domain_txt`, `tiktok_console_inspect`,
`tiktok_domain_verify`, `tiktok_api_smoke`, `tiktok_docs_checklist`.

#### Docker Playwright MCP (required for browser automation)

The Cursor Playwright **plugin** (`npx @playwright/mcp`) looks for Google Chrome and
fails with `Chromium distribution 'chrome' is not found`. Use the **Docker** MCP
instead (already in `.devin/mcp_config.json` and project `.cursor/mcp.json`):

- Image: `mcr.microsoft.com/playwright/mcp:latest`
- Browser: `--browser chromium`
- Profile: `.playwright-data/profile`
- Config: `.playwright-data/pw-mcp-config.json`

After adding/updating `.cursor/mcp.json`, reload MCP servers in Cursor so
`plugin-playwright` is replaced by the Docker `playwright` server.

TikTok browser sidecar (`:9224`) also runs Playwright in Docker. New helpers:

- `POST /browse` `{ "url": "https://www.tiktok.com/..." }` — navigate logged-in page
- `GET /screenshot` — PNG base64 of current page

**MEDIA_UPLOAD inbox drafts** from Content Posting API appear in the **TikTok
mobile app inbox**, not reliably on tiktok.com web. Web Playwright can confirm
login/session but finishing the draft usually requires the phone app.

`TIKTOK_DEV_EMAIL` / `TIKTOK_DEV_PASSWORD` are for the **developers.tiktok.com**
portal (and optionally tiktok.com if the same password works). If sidecar ensure
returns `Username or password doesn't match`, update the TikTok.com password or
log in once via noVNC / Playwright MCP interactively.

### Agent workflow

1. `check-config.py` / `tiktok_check_config` — fix `.env` redirect/scopes drift first
2. `sidecar-session.py ensure` if privacy/browser APIs needed
3. `domain-verify.py` for photo `PULL_FROM_URL` / Direct Post domain gate
4. Do **not** submit app audit unless user explicitly asks; report readiness only
5. Prefer Playwright Docker (`mcr.microsoft.com/playwright:v1.62.1`) for console; dismiss cookie banner before clicks
6. After console URL/redirect edits, re-run SocialAuto OAuth reconnect if scopes/URI changed

### Session recovery ladder (2026-10 update)

When `GET :9224/session` reports `logged_in:false` / `reason:no_session`, try in order:

1. **Stored web cookies** — `social_accounts.meta_data->'tiktok_web_cookies'` in
   Postgres holds a full real cookie set (`sessionid`, `sid_tt`, `uid_tt`,
   `msToken`, …). `sidecar-session.py restore` pulls it and `POST /session`s it —
   cookies stay valid for months and this restored the session 2026-10-02.
2. **Sidecar native QR** — `sidecar-session.py qr` calls the new
   `POST :9224/login/qr` (returns the QR as base64 PNG) and polls
   `GET :9224/login/qr/status`. Faster than the bridge flow, BUT the sidecar is
   headless — see the headless-QR caveat below.
3. **Headed bridge QR** (reliable, documented below) — the noVNC bridge at
   `:9223` with a real Xvfb display.
4. `sidecar-session.py ensure` — credential login; CAPTCHA/rate-limit-prone.

**Persistence**: since PR #246 the sidecar writes `/data/tiktok-session.json`
on successful `POST /session` and on QR approval, and reloads it at startup —
container restarts no longer drop the login (previously sessions were
memory-only and silently vanished on every restart).

**Headless-QR caveat (verified 2026-10-02)**: TikTok authorizes QR logins only
from trusted browser contexts. From the headless sidecar, the QR renders and
the app-side scan registers ("QR code scanned"), but the mobile **Confirm**
is silently ignored — the page never navigates, no `sessionid` is issued, and
the same context later draws a slider CAPTCHA. The `/login/qr` endpoint is
kept for diagnostics; expect it to fail — use stored cookies or the headed
bridge instead.

**Console truth on domain verification**: `cloudless.gr` is listed under
**Verified properties → Domain** in the app's **Production** URL properties
(verified via console DOM 2026-10-02). No `tiktok-domain-verification` TXT
record exists in DNS and none is needed — the verification was completed
console-side. Historical `url_ownership_unverified` digest errors predate the
verification. `domainTokenPresent:false` from `console-inspect.py` is expected
— the token only appears while a verification is pending.

### Sidecar login via QR (no password needed)

When `TIKTOK_DEV_PASSWORD` doesn't match tiktok.com (it is the **developer
portal** password) and no live session exists anywhere:

**Do NOT use the headless MCP playwright browser or the headless sidecar for
QR login — TikTok rejects QR authorization from headless Chromium.** Use the
headed `browser-novnc` bridge (port 9223, Xvfb):

1. `POST localhost:9223/session/start` `{platform:"tiktok", force:true}` with
   `X-Platform: tiktok`
2. `POST /session/navigate` → `https://www.tiktok.com/login/qrcode`
3. Show the QR — extract via `/session/evaluate`:
   `document.querySelector("canvas").toDataURL()` → decode → display, or point
   the user at the live view `http://localhost:6080/vnc.html?autoconnect=1`
   (the PNG expires in ~2 min; noVNC always shows the current code)
4. User scans in TikTok app (profile → ⋯ → scan) → **Confirm login** on phone
   → page navigates to `/foryou`
5. `POST /session/extract` → real `.tiktok.com` cookies (`sessionid`,
   `sid_tt`, `uid_tt`, `ttwid`, `msToken`)
6. `POST localhost:9224/session` `{session_id, cookies}` → verify
   `GET /session` returns `logged_in: true`

Automated: `scripts/tiktok_qr_watch.py` does steps 4–6 — polls `/session/evaluate`
(tagged, keeps the busy-hold warm), extracts on `/foryou` navigation, verifies
`sessionid`/`sid_tt` are present, injects into the sidecar.

Gotchas (learned the hard way):

- **Poller preemption**: `social-worker-messenger` tasks (facebook/threads/
  personal) force-start the bridge and will kill the QR page mid-scan. For a
  clean window: `docker compose stop social-worker-messenger`, restart after.
- **Cookie name collision**: `sessionid` exists on both `instagram.com` and
  `tiktok.com` — the bridge extract used to flatten by name only and could
  inject Instagram's sessionid into the TikTok sidecar. Extract is now
  domain-scoped (2026-09); never hand-copy `sessionid` without checking the
  source domain.
- **False login detection**: a URL change alone is not login — a preempting
  poller navigates the page too. Only trust `sessionid`/`sid_tt` present on
  `.tiktok.com` cookies.
- "Continue with Facebook" on tiktok.com/login is a JS handler that silently
  no-ops in headless Chromium (no popup, no navigation) — don't rely on it.
- Password logins hit "maximum number of attempts" per-IP quickly; the QR path
  avoids the captcha/rate-limit wall entirely.
- The bridge's persistent profile keeps tiktok cookies across restarts — once
  logged in, later `ensure_session("tiktok")` cycles re-authenticate on their
  own (the `/foryou` success pattern triggers extract again).

### Analytics scraping (yt-dlp, not DOM)

Post analytics use **yt-dlp** (`sync_tiktok_account` in
`social-automation/backend/app/services/analytics_sync.py`), not the sidecar
grid DOM — TikTok's signed item-list API rejects headless Chromium ("Something
went wrong" on the grid even when profile stats render).

- `yt-dlp` (`extract_flat`) on `https://www.tiktok.com/@<user>` returns
  view/like/comment/share/**save** counts per video in one pass — richer than
  Display API `video/query` and covers MEDIA_UPLOAD inbox posts + phone posts.
- Cookies required (empty listing without them): the session cookie map is
  persisted on `SocialAccount.meta_data['tiktok_web_cookies']`, written to a
  temp Netscape file per sync. Refresh by re-running the QR login + extract.
- Snapshots land as `source="tiktok_scrape"`; videos with no local PostTarget
  get `post_id=None` (still visible in analytics).
- yt-dlp flat extraction does **not** return follower count — sidecar
  `GET /profile/videos` stats (followers/following/likes render reliably)
  fill `FollowerSnapshot`.
- Evaluated alternatives: TikTok-Api (davidteather) — public data only,
  heavier (own Playwright pool); Douyin_TikTok_Download_API — full self-hosted
  REST service, overkill for now; drawrowfly/tiktok-scraper — dead since 2023.

### TikTok DMs (web drawer UI — no per-thread URLs)

tiktok.com/messages is a single-page drawer — clicking a conversation does
**not** change the URL (`/messages/<id>` routes don't exist). Current DOM:

- Conversation rows: `div[data-e2e="dm-new-conversation-item"]` — thread id in
  `data-conv-id` (e.g. `0:1:<uid>:<conv>`), nickname in
  `[data-e2e="dm-new-conversation-nickname"]`
- Open chat: message list `[data-e2e="dm-new-message-list"]`, bubbles
  `[data-e2e="dm-new-chat-item"]`, time separators
  `[data-e2e="dm-new-time-separator"]`, composer
  `[data-e2e="dm-new-input-editor"]` (Draft.js contenteditable)
- Own messages: `DivChatItemWrapper` without `chat-avatar` child /
  `align-items: flex-end` on the vertical container
- To read/send a thread: navigate to `/messages`, **click** the row matching
  `data-conv-id`, wait ~3s for the drawer — implemented in
  `BrowserBridgeClient.get_tiktok_dm_messages` / `send_tiktok_dm_message`
- Some message types (videos, stickers, shares) render on web as
  `[This message type isn't supported. Download TikTok app…]` — expected, the
  phone app shows them

### Related

- `.devin/skills/developer-apps-ops/tiktok-dev-console/` — app/org/audit reference
- `.devin/skills/tiktok-console-ops/tiktok-publish/` — FILE_UPLOAD / spam / publish modes
- Playwright Docker MCP already in `.devin/mcp_config.json` (`playwright`)

## TikTok Publish

Publish video and photo content to TikTok through the SocialAuto backend.

### When to use

- Create and publish a TikTok video post (FILE_UPLOAD or PULL_FROM_URL)
- Create and publish a TikTok photo carousel post (PULL_FROM_URL only)
- Debug TikTok publish failures (spam limits, domain verification, token issues)
- Cancel stuck pending TikTok uploads
- Check TikTok publish status and poll for completion
- Build slideshow videos from images for TikTok

### TikTok account

| Field | Value |
|-------|-------|
| Platform | tiktok |
| Display name | cloudless.gr |
| Account type | person |
| Sandbox user | cloudless-dev (target: user3113682023385) |
| Token lifetime | 24 hours (refreshed daily by beat task) |
| Granted scopes | `user.info.basic`, `user.info.profile`, `user.info.stats`, `video.list`, `video.publish`, `video.upload` (all 6 granted 2026-09-29 via OAuth reconnect — consent screen grants unlisted scopes; no console approval needed) |
| Client key env | `TIKTOK_CLIENT_KEY` |
| Redirect URI | `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback` (HTTPS required) |

Find the current account ID with:
```bash
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/list-accounts.py | grep tiktok
```

### Publish modes

| Mode | Description | Requires audit? |
|------|-------------|----------------|
| `MEDIA_UPLOAD` | Sends video to TikTok inbox for creator to post manually | No |
| `DIRECT_POST` | Posts directly to the creator's profile | Yes — app must be audited |

**Direct Post audit: under review (submitted 2026-09-29)** — the default
is `DIRECT_POST` with `privacy_level: PUBLIC_TO_EVERYONE` (validated
against `creator_info`'s `privacy_level_options` at publish time). While
the audit is pending, init returns
`unaudited_client_can_only_post_to_private_accounts` and the code
automatically retries the init as `MEDIA_UPLOAD`, so posts still land as
inbox drafts. `MEDIA_UPLOAD` stays available as a per-post override when
the post needs TikTok's native editor (commercial music library,
stickers) — its drafts land in the **mobile app inbox**, not reliably on
web.

### Media transfer methods

| Method | Video | Photo | Domain verification? |
|--------|-------|-------|---------------------|
| `FILE_UPLOAD` | Yes (read local file, upload bytes) | No | Not needed |
| `PULL_FROM_URL` | Yes (TikTok downloads from URL) | Yes (only method) | Required |

#### FILE_UPLOAD (preferred for videos)

SocialAuto's `_publish_tiktok` in `app/services/publishing.py` automatically
uses `FILE_UPLOAD` when a local video file is available (`.mp4`, `.mov`,
`.webm`). This bypasses the domain verification requirement entirely.

The flow:
1. `init_video_upload(source=FILE_UPLOAD, video_size=N)` → returns `upload_url`
2. `upload_video_file(upload_url, video_bytes)` → PUT chunks to TikTok
3. Poll `check_publish_status(publish_id)` until `SEND_TO_USER_INBOX`

#### PULL_FROM_URL (required for photos)

Photo posts only support `PULL_FROM_URL`. The domain serving the images must
be verified in the TikTok developer console. See the `developer-apps-ops` skill
for domain verification instructions.

### Spam protection: 5 pending shares per 24h

TikTok limits API uploads to **5 pending shares within any 24-hour period**.
Each `MEDIA_UPLOAD` init creates a pending share in the creator's TikTok inbox.
If the creator doesn't post or discard them, the limit is hit.

Error: `spam_risk_too_many_pending_share`

#### Clearing pending shares

1. **From the TikTok mobile app**: Open TikTok → Inbox/Drafts → post or delete each pending upload.
2. **Via the cancel API**: Use the cancel script with a known publish_id:
   ```bash
   .devin/skills/tiktok-console-ops/tiktok-publish/scripts/cancel-upload.py <publish_id>
   ```
3. **Wait 24 hours**: Pending shares expire automatically after 24h.

#### Preventing spam lockout

- Always delete failed SocialAuto posts after debugging (prevents beat task retries).
- Check pending count before bulk publishing:
  ```bash
  .devin/skills/tiktok-console-ops/tiktok-publish/scripts/check-pending.py
  ```
- The Celery beat task `check_scheduled_posts` will re-process scheduled posts
  every 30s — if a post is stuck in `scheduled` status, it keeps retrying and
  flooding TikTok's inbox. Delete or mark failed posts to stop this.

### Publish ID format

TikTok FILE_UPLOAD publish IDs use the format `v_inbox_file~v2.<numeric_id>`,
which includes `~` and `.` characters. The `_ID_RE` regex in
`app/services/tiktok_api.py` accepts these: `^[a-zA-Z0-9_\-~.]+$`.

PULL_FROM_URL publish IDs are simpler alphanumeric strings.

### Upload URL hosts

TikTok returns regional upload hosts (e.g. `open-upload-i18n.tiktokapis.com`,
`open-upload.tiktokapis.com`). The `upload_video_file` method accepts any
`*.tiktokapis.com` host.

### Creating a TikTok post via SocialAuto API

```bash
## Create a video post (FILE_UPLOAD, MEDIA_UPLOAD mode)
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

## Then publish:
.devin/skills/socialauto-publish/scripts/publish-post.py <post-id>
```

### Building slideshow videos from images

TikTok requires video for FILE_UPLOAD. To convert carousel slides into a
slideshow video, use ffmpeg in the `comfyui` container (the only container
with ffmpeg installed):

```bash
## 1. Download slides to comfyui's input mount
INPUT="/home/tbaltzakis/cu130-slim/storage-user/input/tiktok-slides"
mkdir -p "$INPUT"
curl -s -o "$INPUT/slide-1.png" "https://social.cloudless.gr/api/v1/media/view?path=<storage_path>"

## 2. Build slideshow (3s per slide, 1080x1080 square for TikTok)
docker exec social-media-comfyui-gpu bash -c '
cd /home/user/ComfyUI/input/tiktok-slides
printf "file '\''%s'\''\nduration 3\n" slide-{1..5}.png > /tmp/slideshow.txt
## Repeat last frame (concat demuxer requirement)
echo "file '\''slide-5.png'\''" >> /tmp/slideshow.txt
## TikTok requires ≥23 FPS — force fps=30 (+ silent AAC) or processing hangs.
ffmpeg -y -f concat -safe 0 -i /tmp/slideshow.txt \
  -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -vf "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p,fps=30" \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 128k -shortest \
  /home/user/ComfyUI/output/cloudless-tiktok-slideshow.mp4
'

## 3. Copy to host and upload to media library
cp storage-user/output/cloudless-tiktok-slideshow.mp4 /tmp/
## Then upload via /api/v1/media/upload
```

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## Cancel a pending TikTok upload by publish_id
.devin/skills/tiktok-console-ops/tiktok-publish/scripts/cancel-upload.py <publish_id>

## Check how many pending shares exist (queries TikTok status for known IDs)
.devin/skills/tiktok-console-ops/tiktok-publish/scripts/check-pending.py

## Poll a post's publish status until complete or failed
.devin/skills/tiktok-console-ops/tiktok-publish/scripts/poll-status.py <post-id>

## Build a slideshow video from image assets in the media library
.devin/skills/tiktok-console-ops/tiktok-publish/scripts/build-slideshow.py <asset-id-1> [<asset-id-2> ...]
```

### Common errors and fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `url_ownership_unverified` | Domain not verified for PULL_FROM_URL | Verify domain in TikTok dev console, or use FILE_UPLOAD for videos |
| `spam_risk_too_many_pending_share` | 5+ pending uploads in 24h | Clear pending from TikTok app, cancel via API, or wait 24h |
| Stuck `PROCESSING_UPLOAD` | Video <23 FPS, bad codec, or tiny slideshow at 1 FPS | Rebuild with `fps=30` + H.264 yuv420p (see build-slideshow.py) |
| `unaudited_client_can_only_post_to_private_accounts` | DIRECT_POST without app audit | Use MEDIA_UPLOAD mode instead |
| `Invalid publish_id format` | Regex rejected `~` or `.` in publish_id | Fixed — regex now accepts `~` and `.` |
| `upload_url must use the TikTok upload host` | Host validation too strict | Fixed — accepts any `*.tiktokapis.com` host |
| `access_token_invalid` | 24h token expired | Refresh token or reconnect account |

### Code references

- `app/services/publishing.py` — `_publish_tiktok()` function (FILE_UPLOAD + PULL_FROM_URL)
- `app/services/tiktok_api.py` — `TikTokAPIClient` class (init, upload, status, cancel)
- `app/api/auth.py` — `TikTokOAuth2` class (client_key, PKCE, comma-separated scopes)
- `app/worker/tasks/token_refresh.py` — `refresh_expiring_tokens` beat task
