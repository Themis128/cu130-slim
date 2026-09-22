---
name: tiktok-console-ops
description: >-
  Operates TikTok for Developers console and SocialAuto TikTok runtime per
  official Content Posting / Login Kit docs: domain verification (PULL_FROM_URL),
  DNS TXT via Cloudflare, browser sidecar session, app audit readiness, and
  config drift checks (redirect URI, scopes, MEDIA_UPLOAD vs DIRECT_POST).
  Use when the user mentions TikTok console, url_ownership_unverified, domain
  verify, app audit, DIRECT_POST, sidecar session, or TikTok MCP tools.
---

# TikTok console ops

Official docs (Context7 `/websites/developers_tiktok` or developers.tiktok.com):

- Content Posting get-started / media transfer — `PULL_FROM_URL` needs verified domain
- Photo posts require `PULL_FROM_URL` (domain verify mandatory)
- Videos can use `FILE_UPLOAD` (no domain verify) or `PULL_FROM_URL`
- `DIRECT_POST` needs approved app audit; until then use `MEDIA_UPLOAD`
- Login Kit: `client_key`, PKCE S256, comma-separated scopes, HTTPS redirect only

## Cloudless defaults

| Field                    | Expected                                                         |
| ------------------------ | ---------------------------------------------------------------- |
| App name / ID            | Cloudless / `7630494700880906241`                                |
| Redirect URI             | `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback`  |
| Website URL (audit)      | `https://cloudless.gr` (public site — not `social.cloudless.gr`) |
| Web / media domain       | `cloudless.gr` (covers `social.cloudless.gr`)                    |
| Connected account        | sandbox `user3113682023385` / brand cloudless.gr                 |
| Publish mode (pre-audit) | `MEDIA_UPLOAD`                                                   |
| Sidecar                  | `http://127.0.0.1:9224`                                          |
| Domain verify status     | `cloudless.gr` already verified in Production URL properties     |

**Drift to fix if seen in console:** `social.cloudless.jp` web URL or redirect — replace with `.gr` SocialAuto paths above.

## Env (never print secrets)

- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`
- `TIKTOK_DEV_EMAIL`, `TIKTOK_DEV_PASSWORD` — developer portal login
- `CLOUDFLARE_API_TOKEN` — DNS TXT for `tiktok-domain-verification=…`
- Site TXT `tiktok-developers-site-verification=…` is **not** Content Posting domain verify
- `domain-verify.sh` now short-circuits when `cloudless.gr` is already listed under **Verified properties**

## Tool scripts (repo root)

```bash
.cursor/skills/tiktok-console-ops/scripts/check-config.sh
.cursor/skills/tiktok-console-ops/scripts/sidecar-session.sh ensure   # Playwright Docker → POST /session
.cursor/skills/tiktok-console-ops/scripts/sidecar-session.sh status
.cursor/skills/tiktok-console-ops/scripts/console-inspect.sh          # login + dump app state
.cursor/skills/tiktok-console-ops/scripts/domain-verify.sh            # console token → CF TXT → Verify
.cursor/skills/tiktok-console-ops/scripts/dns-tiktok-txt.sh list|add <token>
```

Audit-fix helpers (Playwright Docker, under `scripts/lib/`):

- `rejection-reason.mjs` — click **See why** and dump reviewer notes
- `draft-fix-website-url.mjs` / `upload-icon-submit.mjs` — return to draft, set Website URL to `https://cloudless.gr`, restore app icon, submit
- Prefer Terms/Privacy: `https://cloudless.gr/en/terms` and `https://cloudless.gr/en/privacy`

## MCP server

`tiktok-console` in `.devin/mcp_config.json` →
`.cursor/skills/tiktok-console-ops/scripts/tiktok-console-mcp-server.py`

Tools: `tiktok_check_config`, `tiktok_sidecar_status`, `tiktok_sidecar_ensure_session`,
`tiktok_dns_list`, `tiktok_dns_add_domain_txt`, `tiktok_console_inspect`,
`tiktok_domain_verify`, `tiktok_api_smoke`, `tiktok_docs_checklist`.

### Docker Playwright MCP (required for browser automation)

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

## Agent workflow

1. `check-config.sh` / `tiktok_check_config` — fix `.env` redirect/scopes drift first
2. `sidecar-session.sh ensure` if privacy/browser APIs needed
3. `domain-verify.sh` for photo `PULL_FROM_URL` / Direct Post domain gate
4. Do **not** submit app audit unless user explicitly asks; report readiness only
5. Prefer Playwright Docker (`mcr.microsoft.com/playwright:v1.62.1`) for console; dismiss cookie banner before clicks
6. After console URL/redirect edits, re-run SocialAuto OAuth reconnect if scopes/URI changed

## Sidecar login via QR (no password needed)

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

Automated: `scripts/tiktok_qr_watch.sh` does steps 4–6 — polls `/session/evaluate`
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

## Analytics scraping (yt-dlp, not DOM)

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

## TikTok DMs (web drawer UI — no per-thread URLs)

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

## Related

- `.devin/skills/tiktok-dev-console/` — app/org/audit reference
- `.devin/skills/tiktok-publish/` — FILE_UPLOAD / spam / publish modes
- Playwright Docker MCP already in `.devin/mcp_config.json` (`playwright`)
