---
name: social-profile-update
description: >-
  Update social profile fields (bio, links, website, description, avatar, cover) across all platforms via API/sidecar, plus the 5-second profile audit checklist. Use for any profile-field change or profile-quality audit.
---

# Social Profile Update

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Social Profile Update | `social-profile-update` |
| SocialAuto Profile | `social-profile-update` → `socialauto-profile/` |
| Profile 5-Second Test Audit | `social-profile-update` → `profile-5sec-test/` |

## Social Profile Update

Update business social profiles programmatically across all 6 platforms
supported by SocialAuto. Credentials are managed through the
`social-accounts-manager` skill and stored in the Cloudflare-first secret store
(`/api/v1/secrets`). All logins should be initiated from the SocialAuto app.

### Platform capability matrix

| Platform | Bio/Description | Profile picture | Cover/Banner | Name | Website | Method |
|----------|:-:|:-:|:-:|:-:|:-:|--------|
| **LinkedIn (Company Page)** | YES | YES | YES | YES | YES | Official API — `POST /organizations/{id}` with `rw_organization_admin` scope |
| **LinkedIn (Personal)** | NO | NO | NO | NO | NO | Official API is read-only for profiles (Partner-only write access) |
| **Facebook (Page)** | YES | YES | YES | NO | YES | Official Graph API — `POST /{page_id}` with Page access token + `MANAGE` task |
| **Instagram (Business)** | NO | NO | NO | NO | NO | Official Graph API is read-only for profile fields |
| **Threads** | NO | NO | NO | NO | NO | Official API is read-only (`GET /me` only) |
| **Twitter / X** | NO | NO | NO | NO | NO | Official v2 API removed profile write endpoints; requires unofficial/cookie-based APIs |
| **TikTok** | NO | NO | NO | NO | NO | Official API is read-only for profile fields |

#### Key findings

**Officially writable via API:**
- **LinkedIn Company Page** — full profile update (description, logo, cover, website, name, industries, specialties) via the Organizations API with `rw_organization_admin` scope. Requires ADMINISTRATOR role on the page.
- **Facebook Page** — `about`, `description`, `website`, `phone`, `picture` (profile photo), `cover` (cover photo) via Graph API `POST /{page_id}` with a Page access token that has the `MANAGE` task. Cannot change the page name via API.

**Read-only via official API (no programmatic profile updates):**
- **Instagram Business** — the Graph API only supports `GET` on the IG User node. Fields like `biography`, `profile_picture_url`, `name`, `website` are readable (with proper scopes) but not writable. Profile changes must be done manually in the Instagram app.
- **Threads** — the official API only supports `GET /me` for profile info. No write endpoints for bio, name, or avatar.
- **Twitter / X** — the official v2 API removed the profile update endpoints that existed in v1.1. Updating profile fields requires unofficial approaches (cookie-based private APIs, third-party services like Xquik or TwexAPI).
- **TikTok** — the Display API and Content Posting API are read-only for profile data. No endpoints exist for updating bio, avatar, or display name.

**Unofficial / private API alternatives:**
- **Instagram** — `instagrapi` (Python) provides `account_edit()`, `account_set_biography()`, and `account_change_picture()` via Instagram's private mobile API. Requires username/password login (not OAuth). Risk of account ban.
- **Twitter / X** — `twexapi.io` and `xquik.com` offer paid third-party APIs that update name, bio, location, website, avatar, and banner via cookie-based authentication. Not official, carries risk.
- **Threads** — `threads-go` (Go) and other private API clients can read profiles but profile writes are not reliably supported.

### API details per platform

#### LinkedIn Company Page (official API — writable)

**Prerequisites:**
- OAuth token with `rw_organization_admin` scope
- ADMINISTRATOR role on the target organization
- Organization ID (available in SocialAuto as the LinkedIn org account)

**Update description/about:**
```bash
curl -X POST "https://api.linkedin.com/rest/organizations/{org_id}" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -H "X-Restli-Method: PARTIAL_UPDATE" \
  -H "LinkedIn-Version: 202501" \
  -d '{"description": {"value": "New description here"}}'
```

**Writable fields:**
- `description` — company description
- `localizedDescription` — locale-specific description
- `coverPhotoV2` — cover image (upload via Images API first)
- `logoV2` — company logo (upload via Images API first)
- `specialties` — company specialties
- `website` — company website URL
- `industries` — industry URN list
- `foundedOn` — founding date
- `organizationType` — company type enum

**Read-only fields (cannot update via API):**
- `name` — company name (contact LinkedIn support to change)
- `vanityName` — URL slug (contact LinkedIn support)

#### Facebook Page (official Graph API — writable)

**Prerequisites:**
- Page access token with `MANAGE` task (or `pages_manage_posts` + `pages_show_list`)
- Page ID (available in SocialAuto as the Facebook page account)

**Update page about/description:**
```bash
curl -X POST "https://graph.facebook.com/v21.0/{page_id}" \
  -H "Content-Type: application/json" \
  -d '{"about": "New about text", "description": "New description", "access_token": "{page_token}"}'
```

**Update profile picture:**
```bash
curl -X POST "https://graph.facebook.com/v21.0/{page_id}/picture" \
  -d "url={public_image_url}&access_token={page_token}"
```

**Update cover photo:**
```bash
curl -X POST "https://graph.facebook.com/v21.0/{page_id}" \
  -d "cover={photo_id}&access_token={page_token}"
```

**Writable fields:**
- `about` — short about text
- `description` — longer description
- `website` — website URL
- `phone` — phone number
- `picture` — profile picture (via `/picture` edge with `url` param)
- `cover` — cover photo (photo ID of an uploaded photo)

**Cannot update via API:**
- `name` — page name (must be changed in Facebook UI)
- Page settings like category, address (some require UI)

#### Instagram Business (official API — read-only)

The Instagram Graph API does NOT support profile updates. The `IG User` node
only supports `GET` requests. To update Instagram profile fields:

**Manual method (recommended):**
1. Open the Instagram app or instagram.com
2. Edit Profile → update name, username, bio, website, profile picture

**Unofficial method (instagrapi — use at your own risk):**
```python
from instagrapi import Client
cl = Client()
cl.login("username", "password")
cl.account_set_biography("New bio text")
cl.account_edit(external_url="https://cloudless.gr", full_name="Cloudless")
cl.account_change_picture("/path/to/profile_pic.jpg")
```

**Warning:** `instagrapi` uses Instagram's private mobile API, not the official
Graph API. This can trigger account suspension or ban. Not recommended for
production use.

#### Threads (official API — read-only)

The Threads API only supports `GET /me` for profile info. No write endpoints
exist for bio, name, or profile picture.

**To update Threads profile:**
- Threads shares profile info with Instagram. Update your Instagram profile
  and the Threads profile syncs automatically.
- Or edit manually in the Threads app.

#### Twitter / X (official API — no profile write)

Twitter API v2 does not include profile update endpoints. The v1.1
`account/update_profile` and `account/update_profile_image` endpoints were
deprecated and removed.

**Third-party alternatives (paid, unofficial):**
- **Xquik** (`xquik.com`) — `PATCH /x/profile`, `PATCH /x/profile/avatar`, `PATCH /x/profile/banner`
- **TwexAPI** (`twexapi.io`) — `POST /twitter/profile` with cookie auth

Both require X account cookies (not OAuth tokens) and carry risk of account
suspension. Not recommended for brand accounts.

**Manual method (recommended):**
- Update via X/Twitter settings UI at x.com/settings/profile

#### TikTok (official API — read-only)

The TikTok Display API and Content Posting API do not include profile update
endpoints. Profile fields (bio, avatar, display name) can only be read.

**To update TikTok profile:**
- Edit manually in the TikTok app → Profile → Edit Profile

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## Get current profile info for all connected accounts
.devin/skills/social-profile-update/scripts/get-all-profiles.py

## Update LinkedIn Company Page description/about
.devin/skills/social-profile-update/scripts/update-linkedin-org.py "New description" "New about text"

## Update Facebook Page about/description/website
.devin/skills/social-profile-update/scripts/update-facebook-page.py --about "New about" --description "New desc" --website "https://cloudless.gr"

## Update Facebook Page profile picture from a media library asset
.devin/skills/social-profile-update/scripts/update-facebook-picture.py <media_asset_id>

## Prepare brand-aligned profile text for manual entry on read-only platforms
.devin/skills/social-profile-update/scripts/generate-brand-profile.py
```

### Brand profile reference (cloudless.gr)

Based on the Cloudless brand identity:

- **Name**: Cloudless
- **Tagline**: Clear skies. Zero friction.
- **Bio (short)**: Cloud architecture, serverless development, data analytics & AI-powered digital marketing. Clear skies, zero friction.
- **Bio (longer)**: We help startups and SMBs ship faster with serverless cloud architecture, Cloudflare-first delivery, and AI-powered digital marketing. No lock-in. Transparent pricing. Results in 14 days.
- **Website**: https://cloudless.gr
- **Industry**: Cloud Computing, Serverless & AI Marketing
- **Values**: Innovation, Customer-centricity, Flexibility, Collaboration
- **Messaging pillars**: Serverless simplicity, No lock-in, Transparent pricing, Results in 14 days, Data-driven growth
- **Preferred phrases**: Clear skies, serverless, no lock-in, Cloudflare, transparent pricing, results in 14 days, open-source, data-driven, full control, zero friction
- **Banned phrases**: lock-in, vendor lock-in, enterprise BS, synergy, game-changer, disrupt, revolutionary, cutting-edge
- **Visual style**: Dark navy (#0b1220) backgrounds with teal (#22d3e6) accents. Modern technology aesthetic. Clean, minimalist.

### Important notes

- Always verify the current profile before making changes.
- LinkedIn and Facebook are the only platforms with official write APIs for profile fields.
- Instagram, Threads, Twitter/X, and TikTok profile changes must be done manually or via unofficial/private APIs that carry account-ban risk.
- Never store or commit social media passwords in the repository.
- For LinkedIn org updates, the token must have `rw_organization_admin` scope and the user must be an ADMINISTRATOR of the organization.
- For Facebook Page updates, use a Page access token (not a user token) with the `MANAGE` task permission.
- Profile picture uploads require a publicly accessible URL (Facebook) or a two-step upload via the Images API (LinkedIn).

## SocialAuto Profile

Update profile metadata (bio, avatar, cover, headline, etc.) for connected
social accounts through the unified `/api/v1/profile` API.

### When to use

- Read the current profile for any connected account
- Update bio, headline, about, name, website, location, phone
- Upload a profile picture or cover photo
- Log in to Instagram private API or browser sessions (Facebook/LinkedIn)
- Check profile field support per platform

### API base

```
http://127.0.0.1:8083/api/v1/profile
```

### Authentication

Bearer token from `POST /api/v1/auth/login`.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{account_id}` | Read current profile |
| PUT | `/{account_id}` | Update profile fields |
| POST | `/{account_id}/picture` | Upload profile picture |
| POST | `/{account_id}/cover` | Upload cover/banner photo |
| POST | `/{account_id}/login` | Log in to private API or browser session |

### Platform support

| Platform | Read | Write fields | Picture | Cover | Login method |
|----------|------|--------------|---------|-------|--------------|
| Instagram | ✅ | bio, name, website, phone, email | ✅ | ❌ | `aiograpi-rest` (username+password) |
| Facebook Page | ✅ | about, description, website, phone | ✅ | ✅ | OAuth (already connected) |
| Facebook personal | ✅ | about | ✅ | ❌ | Playwright (username+password) |
| LinkedIn | ✅ | headline, about | ❌ | ❌ | Playwright (username+password) |
| Twitter/X | ✅ | name, bio, location, website | ✅ | ✅ | `tweepy` v1.1 API credentials |
| TikTok | ✅ | name/nickname, bio/signature | ❌ | ❌ | **Blocked** — slider captcha + tt-ticket-guard anti-bot |

### Scripts

Run from repo root `cu130-slim/`:

```bash
## Read a profile
.devin/skills/social-profile-update/socialauto-profile/scripts/get-profile.py <account-id>

## Update profile fields
.devin/skills/social-profile-update/socialauto-profile/scripts/update-profile.py <account-id> '{"about":"..."}'

## Upload a profile picture
.devin/skills/social-profile-update/socialauto-profile/scripts/upload-picture.py <account-id> <image-file>

## Upload a cover/banner photo
.devin/skills/social-profile-update/socialauto-profile/scripts/upload-cover.py <account-id> <image-file>

## Log in to Instagram / Facebook / LinkedIn private API or browser session
.devin/skills/social-profile-update/socialauto-profile/scripts/login.py <account-id> <username> <password>

## List accounts to find account IDs
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/list-accounts.py
```

### Example update payloads

```bash
## Instagram / TikTok bio
.devin/skills/social-profile-update/socialauto-profile/scripts/update-profile.py <id> '{"biography":"Cloud consulting & AI marketing ☁️"}'

## LinkedIn headline and about
.devin/skills/social-profile-update/socialauto-profile/scripts/update-profile.py <id> '{"headline":"Founder @ cloudless.gr","about":"Cloud consulting, serverless & AI marketing."}'

## Facebook personal about
.devin/skills/social-profile-update/socialauto-profile/scripts/update-profile.py <id> '{"about":"Cloud consulting & AI marketing"}'
```

### Important notes

- Profile updates may be ignored by platforms if the account does not have
  the required API tier or permissions. The response lists `updated_fields`
  and `ignored_fields`.
- Browser automation (Facebook/LinkedIn) requires a successful `/login` first
  to store cookies/session state in the account metadata.
- Twitter profile writes require a paid API tier (Basic/Pro) with v1.1
  credentials.
- The `aiograpi-rest` sidecar must be running for Instagram (`docker compose up -d instagram-private-api`).
- **TikTok profile writes are blocked** by TikTok's multi-layer anti-bot system:
  - **Slider captcha**: The Edit Profile dialog triggers a "Drag the slider to
    fit the puzzle" captcha. Synthetic mouse events lack `isTrusted` and are
    rejected. The slider button is disabled until the captcha images load.
  - **Web API** (`POST /api/update/profile/`): Returns
    `tt-ticket-guard-result: 1104` (captcha required) even with valid
    `X-Bogus` signatures (from `window.byted_acrawler.frontierSign()`) and
    `msToken` cookies. The request body requires `signature` (bio text) and
    `tt_csrf_token`.
  - **Private mobile API** (`api-h2.tiktokv.com`): Returns HTTP 403.
  - Profile reads via the official Display API work correctly.
  - To update the TikTok bio manually: open the TikTok app or website →
    Edit Profile → change Bio → solve the slider captcha → Save.

## Profile 5-Second Test Audit

Scores every connected SocialAuto account against the profile-conversion
checklist (source: Sofia Kakkava, Visibility Era Day 4 — ingested into the
team Chroma collection as `doc:visibility-era-day4:*`):

- **Name present + consistent** — same display name on every brand account
- **Handle typeable + on-brand** — no random digits/underscores
- **Bio present** and mentions/links the site
- **Website link set**
- **Avatar present**, then **vision-scored** by DMR `ai/qwen3-vl`:
  - personal accounts → face fills frame, lit, no sunglasses/filters
  - brand accounts → crisp mark readable as a 40px circle icon

### Run

```bash
## from repo root — needs a bearer token (admin has 2FA; mint one with TOTP)
SA_TOKEN=<token> python3 .devin/skills/social-profile-update/profile-5sec-test/scripts/profile_audit.py
SA_TOKEN=<token> python3 ... --json          # machine-readable
SA_EMAIL=.. SA_PASSWORD=.. SA_OTP=.. python3 ...  # creds login instead
```

Env: `SOCIAL_API_URL` (default `http://localhost:8083/api/v1`),
`DMR_URL` (default `http://localhost:12435/engines/v1`),
`DMR_VISION_MODEL` (default `ai/qwen3-vl`).

### Hard platform limits discovered (2026-09-21)

- **Instagram website/name are APP-ONLY.** The web edit page renders the
  Website input `disabled` with "Editing your links is only available on
  mobile." The new edit form has no Name field at all. instagrapi password
  login is currently blocked server-side ("this version of Instagram is out
  of date" — even on aiograpi 2.0.8). FB-SSO sessionids get `login_required`
  on mobile-API calls (web session ≠ API session trust). Bottom line: IG
  name/website edits require the mobile app; don't burn time on web paths.
- **Threads** has no writable website field (PUT ignores it); the bio text
  carries the link. Its `full_name` is inherited from the linked Instagram
  account — changing it requires the IG mobile-app name edit (same limit
  as above); Threads itself exposes no name field.
- **Facebook personal `/profile` scrape exceeds 60s** (up to 3 navigations
  × 20s settle) — the audit passes `timeout=150` for profile reads; a
  "timed out" error on that account is scrape latency, not auth failure.
  The sidecar's own `/session` check (`logged_in`) is the fast probe.
- **TikTok** profile writes are captcha-blocked (tt-ticket-guard); website
  needs a business account anyway.
- **Twitter/X handle** (@screen_name) is not API-writable — account settings
  only. `full_name`, bio, location, website ARE writable via PUT.
- **Facebook profile picker** after session loss can't be synthetic-clicked;
  transplant `c_user`/`xs` cookies from the MCP browser (see
  session-transplant skill) — verified working.
- **LinkedIn sidecar** opens a circuit on 429s (check `/health`
  `rate_limit_until`) — profile reads/writes error until it clears.

### Browser bridge IG-edit selectors (fixed 2026-09-21)

IG's edit form uses `placeholder` attrs, not `name`/`aria-label`:
Website → `input[placeholder="Website"]`, Bio → `textarea#pepBio` /
`textarea[placeholder="Bio"]`, Submit → `div[role="button"]:has-text("Submit")`.
`browser-novnc/browser-bridge.py` `update_instagram_profile` now includes
these alongside the legacy selectors.

### IG session bootstrap recipe (verified 2026-09-21)

1. FB sidecar must be logged in — if it shows the picker, transplant
   `c_user`/`xs` from the MCP playwright browser via `POST :9226/session`
   `{storage_state}`.
2. `login-via-facebook.py cloudless.gr` drives FB→IG SSO on the sidecar and
   lands a live **web** session (good for reads/browsing; NOT for instagrapi).
3. To reuse that session in the 9223 bridge: `GET :9226/debug/all-cookies?
   domain=instagram.com` → `POST :9223/session/cookies` with playwright-format
   objects. Injection into a running context works; cookies do NOT survive a
   bridge container restart (re-inject after restarts).
