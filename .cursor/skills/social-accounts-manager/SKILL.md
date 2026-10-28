---
name: social-accounts-manager
description: >-
  Manage connected SocialAuto accounts end-to-end: account inventory, per-platform profile field updates (bio, website, avatar, cover), OAuth connection flows, and where credentials/secrets live. Use for adding, auditing, or editing connected accounts.
---

# Social Accounts Manager

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Social Accounts Manager | `social-accounts-manager` |
| SocialAuto Accounts | `social-accounts-manager` → `socialauto-accounts/` |
| Social OAuth Operations | `social-accounts-manager` → `social-oauth-ops/` |
| Social Profile Secrets | `social-accounts-manager` → `social-profile-secrets/` |

## Social Accounts Manager

Update profile fields (website, bio, links, contact info) across ALL
connected social accounts using the correct method per platform.

### Connected accounts

| Platform | Account | SocialAuto ID | Method |
|----------|---------|---------------|--------|
| Facebook Page | cloudless.gr | `ca22c266-4a93-47bd-b3ed-32b38d0ffa7b` | Graph API (Page token) |
| Facebook personal | Themistoklis Baltzakis | `a8ca7769-60a9-413d-ac59-5b16f7dd12a4` | FB browser sidecar (port 9226) |
| LinkedIn Organization | cloudless.gr | `57e9ed31-7454-4979-9e6b-efcb35d2d78e` | LinkedIn sidecar (port 9225) |
| LinkedIn personal | Themistoklis Baltzakis | `e17ee097-8ff9-46f4-9422-b0c59e27b7a6` | LinkedIn sidecar (port 9225) |
| Instagram Business | cloudless.gr | `31822deb-fbd5-4b7b-a5fd-5ca864783cf7` | instagrapi sidecar (port 8011) |

### Platform capability matrix

| Platform | Bio/About | Website | Profile pic | Cover | Links in bio | Method |
|----------|:-:|:-:|:-:|:-:|:-:|--------|
| **Facebook Page** | YES (100 char about + long description) | YES (single URL) | YES | YES | In description | Graph API `POST /{page_id}` |
| **Facebook personal** | YES (bio) | YES (contact info) | YES | YES | In bio | FB browser sidecar |
| **LinkedIn Org** | YES (tagline + description, 2000 chars) | YES (single URL) | YES | YES | In description | LinkedIn sidecar admin edit |
| **LinkedIn personal** | YES (about section) | YES (contact info, multiple URLs) | YES | YES | In about | LinkedIn sidecar profile edit |
| **Instagram Business** | YES (150 chars, emojis count as 2) | YES (single external_url) | YES | NO | NO (use external_url) | instagrapi sidecar |

### Professional links to use

```
Website:     https://cloudless.gr
Portfolio:   https://baltzakisthemis.com
WhatsApp:    https://wa.me/306977777838
LinkedIn:    https://www.linkedin.com/company/cloudless-gr
```

### API base URLs

```
SocialAuto API:    http://localhost:8083/api/v1
FB sidecar:        http://localhost:9226
LinkedIn sidecar:  http://localhost:9225
Instagram sidecar: http://localhost:8011
```

### Authentication

```bash
TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=tbaltzakis@cloudless.gr&password=<password>" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

### Tool scripts

All scripts run from the repo root `cu130-slim/`:

#### Universal (all platforms)

```bash
## Add professional links to ALL connected accounts
.devin/skills/social-accounts-manager/scripts/add-links-all.py

## Read ALL profiles and show current state
.devin/skills/social-accounts-manager/scripts/read-all.py

## Sync profile info from LinkedIn to all other platforms
.devin/skills/social-accounts-manager/scripts/sync-from-linkedin.py
```

#### Facebook Page

```bash
## Update Facebook Page website
.devin/skills/social-accounts-manager/scripts/fb-page-update-website.py "https://cloudless.gr"

## Update Facebook Page about (100 char limit)
.devin/skills/social-accounts-manager/scripts/fb-page-update-about.py "About text"

## Update Facebook Page long description
.devin/skills/social-accounts-manager/scripts/fb-page-update-description.py "Long description"

## Update Facebook Page profile picture
.devin/skills/social-accounts-manager/scripts/fb-page-update-picture.py /path/to/image.png

## Update Facebook Page cover photo
.devin/skills/social-accounts-manager/scripts/fb-page-update-cover.py /path/to/cover.png
```

#### Facebook Personal

```bash
## Update Facebook personal profile bio
.devin/skills/social-accounts-manager/scripts/fb-personal-update-bio.py "Bio text"

## Update Facebook personal contact info (website)
.devin/skills/social-accounts-manager/scripts/fb-personal-update-website.py "https://cloudless.gr"

## Update Facebook personal profile picture
.devin/skills/social-accounts-manager/scripts/fb-personal-update-picture.py /path/to/image.png
```

#### LinkedIn Organization

```bash
## Update LinkedIn org tagline
.devin/skills/social-accounts-manager/scripts/li-org-update-tagline.py "Tagline text"

## Update LinkedIn org description (2000 char limit)
.devin/skills/social-accounts-manager/scripts/li-org-update-description.py "Description text"

## Update LinkedIn org website
.devin/skills/social-accounts-manager/scripts/li-org-update-website.py "https://cloudless.gr"

## Update LinkedIn org specialties
.devin/skills/social-accounts-manager/scripts/li-org-update-specialties.py "Cloud, AI, Software"

## Update LinkedIn org logo
.devin/skills/social-accounts-manager/scripts/li-org-update-logo.py /path/to/logo.png

## Update LinkedIn org cover
.devin/skills/social-accounts-manager/scripts/li-org-update-cover.py /path/to/cover.png
```

#### LinkedIn Personal

```bash
## Update LinkedIn personal headline
.devin/skills/social-accounts-manager/scripts/li-personal-update-headline.py "Headline text"

## Update LinkedIn personal about section
.devin/skills/social-accounts-manager/scripts/li-personal-update-about.py "About text"

## Update LinkedIn personal contact info (website)
.devin/skills/social-accounts-manager/scripts/li-personal-update-website.py "https://cloudless.gr"

## Update LinkedIn personal profile picture
.devin/skills/social-accounts-manager/scripts/li-personal-update-picture.py /path/to/image.png

## Update LinkedIn personal cover photo
.devin/skills/social-accounts-manager/scripts/li-personal-update-cover.py /path/to/cover.png
```

#### Instagram Business

```bash
## Login to Instagram sidecar (by sessionid or username/password)
.devin/skills/social-accounts-manager/scripts/ig-login.py

## Update Instagram bio (150 char limit, emojis count as 2)
.devin/skills/social-accounts-manager/scripts/ig-update-bio.py "Bio text"

## Update Instagram external URL
.devin/skills/social-accounts-manager/scripts/ig-update-url.py "https://cloudless.gr"

## Update Instagram profile picture
.devin/skills/social-accounts-manager/scripts/ig-update-picture.py /path/to/image.png

## Read Instagram profile
.devin/skills/social-accounts-manager/scripts/ig-read-profile.py
```

### Important notes

- **Facebook Page About** is limited to 100 characters. Use the description field for longer text.
- **Instagram bio** is limited to 150 characters (emojis count as 2 each in Instagram's count).
- **LinkedIn org description** is limited to 2000 characters.
- **LinkedIn personal About** is limited to 2600 characters.
- **LinkedIn sidecar** requires a logged-in browser session. Use `POST /login` with credentials from the secret store.
- **Instagram sidecar** requires a valid sessionid. Use `login-via-facebook.py` from the `instagram-ops` skill or `POST /auth/login` with username/password.
- **Facebook personal profile** updates require the FB browser sidecar (Graph API doesn't support personal profile writes).
- **LinkedIn personal profile** writes require browser automation (LinkedIn API is read-only for profiles without Partner Program access).
- Never log or commit session IDs, tokens, or passwords.

## SocialAuto Accounts

Manage connected social media accounts and OAuth tokens.

### When to use

- List all connected social accounts and their status
- Check if an account token is valid
- Refresh an expired token
- Sync Facebook Pages or Instagram Business accounts
- Test account connectivity
- Get account details (display name, platform, type, follower count)

### API base

```
http://127.0.0.1:8083/api/v1/accounts
```

### Authentication

Bearer token from `POST /api/v1/auth/login`.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `` | List all connected accounts |
| GET | `/{id}` | Get a single account |
| POST | `/{id}/test` | Test account connectivity |
| POST | `/{id}/refresh` | Refresh OAuth token |
| GET | `/{id}/validate` | Validate token and permissions |
| POST | `/{id}/sync-business-accounts` | Sync Facebook Pages / IG Business |
| POST | `/{id}/set-business-account` | Set active business account |
| POST | `/linkedin/sync-organizations` | Sync LinkedIn organizations |
| POST | `/connect/{platform}` | Get OAuth connect URL |

### Supported platforms

| Platform | Account types | Token refresh |
|----------|--------------|---------------|
| LinkedIn | person, organization | No expiry |
| Twitter/X | person | 2h tokens, refreshed hourly |
| Facebook | user, page | ~60 day long-lived |
| Instagram | business | ~60 day long-lived |
| Threads | person | ~60 day long-lived |
| TikTok | person | 24h tokens, refreshed daily |

### Auto token refresh

A Celery beat task runs every hour at :15 past and refreshes tokens expiring
within 4 hours. If a refresh fails, the account is marked `expired` and
requires manual reconnect from the Accounts page.

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## List all connected accounts
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/list-accounts.py

## Get account details
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/get-account.py <account-id>

## Test account connectivity
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/test-account.py <account-id>

## Refresh account token
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/refresh-account.py <account-id>

## Validate account token and permissions
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/validate-account.py <account-id>

## Sync Facebook/Instagram business accounts
.devin/skills/social-accounts-manager/socialauto-accounts/scripts/sync-business.py <account-id>
```

### Cloudless.gr account IDs

These are the connected accounts for cloudless.gr (check with list-accounts.py
for current IDs):

| Platform | Display name | Type |
|----------|-------------|------|
| LinkedIn | cloudless.gr | organization (Company Page) |
| LinkedIn | Themistoklis Baltzakis | person |
| Facebook | cloudless.gr | page |
| Instagram | (business) | business |
| TikTok | cloudless.gr | person |
| Twitter/X | Themistoklis Baltzakis | person |
| Threads | (person) | person |

### Important notes

- LinkedIn tokens do not expire (no `expires_in` returned).
- TikTok requires HTTPS redirect URIs.
- Facebook stores both user and page accounts; the user token is needed
  for Sync Business.
- If `Sync Business` fails with `(#100) Tried accessing nonexisting field`,
  the stored token is a Page token — disconnect and reconnect Facebook.

## Social OAuth Operations

Day-to-day OAuth operations for all SocialAuto platforms: connect, reconnect,
refresh tokens, debug errors, and verify account health.
Use when connecting accounts, debugging OAuth failures, refreshing expired tokens,
or checking which accounts are connected.

### Supported platforms

| Platform | OAuth flow | PKCE | Token lifetime | Refresh |
|----------|-----------|------|----------------|---------|
| LinkedIn | OAuth 2.0 | No | 60 days | Auto via refresh_token |
| Twitter/X | OAuth 2.0 + PKCE | Yes (S256) | 2 hours | Via refresh_token (offline.access) |
| Facebook | OAuth 2.0 | No | 60 days (user), permanent (page) | Re-exchange |
| Instagram | OAuth 2.0 (via FB) | No | 60 days | Re-exchange |
| Threads | OAuth 2.0 | No | 60 days | Via /refresh_access_token |
| TikTok | OAuth 2.0 + PKCE | Yes (S256) | 24 hours | Via refresh_token |

### Auto token refresh

A Celery beat task `app.worker.tasks.token_refresh.refresh_expiring_tokens` runs every hour at :15 past the hour. It automatically refreshes any active account token expiring within the next 4 hours:

- **TikTok**: 24h tokens — refreshed daily (TikTok doesn't return `expires_in` on refresh, so 24h is assumed).
- **Twitter/X**: 2h tokens — refreshed every hour (requires `offline.access`).
- **Meta (FB/IG/Threads)**: ~60-day tokens — refreshed when within 4h of expiry.
- **LinkedIn**: tokens don't expire (no `expires_in`).

If a refresh fails, the account is marked `expired` — but the task now
**retries `expired` accounts unconditionally on every run** (auto-heal,
Sept 2026), as long as a `refresh_token_enc` is stored. A successful
retry restores `active` automatically, so a transient failure no longer
sticks forever. Only an `expired` account with **no stored refresh
token** needs manual reconnect from the Accounts page.

Trigger manually:
```bash
docker compose exec -T social-worker-publishing celery -A app.worker.celery_app call app.worker.tasks.token_refresh.refresh_expiring_tokens
```

### Common operations

#### Connect an account

```bash
## Via the API (body-based endpoint)
curl -X POST http://localhost:8083/api/v1/accounts/connect \
  -H "Authorization: Bearer {JWT}" \
  -H "Content-Type: application/json" \
  -d '{"platform": "twitter"}'
## Returns: {"authorization_url": "https://..."}

## Via the API (path-based endpoint)
curl -X POST "http://localhost:8083/api/v1/accounts/connect/twitter?team_id={TEAM_ID}" \
  -H "Authorization: Bearer {JWT}"
```

Or use the frontend at http://localhost:8082/accounts.

#### List connected accounts

```bash
curl http://localhost:8083/api/v1/accounts \
  -H "Authorization: Bearer {JWT}"
```

#### Check account health

```bash
curl http://localhost:8083/api/v1/accounts/{account_id}/health \
  -H "Authorization: Bearer {JWT}"
```

#### Reconnect an expired account

1. Delete the old connection:
   ```bash
   curl -X DELETE http://localhost:8083/api/v1/accounts/{account_id} \
     -H "Authorization: Bearer {JWT}"
   ```
2. Reconnect via the Connect button or API.

#### Verify OAuth URL for a platform

```bash
## Meta platforms
python3 .devin/skills/developer-apps-ops/meta-oauth-setup/scripts/verify-oauth-urls.py

## Twitter/X
python3 .devin/skills/developer-apps-ops/twitter-oauth-setup/scripts/verify-oauth-url.py
```

### Backend OAuth endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/auth/oauth/{platform}` | GET | Generate authorize URL (general) |
| `/api/v1/accounts/connect` | POST | Generate authorize URL (body-based) |
| `/api/v1/accounts/connect/{platform}` | POST | Generate authorize URL (path-based) |
| `/api/v1/auth/oauth/{platform}/callback` | GET | OAuth callback + token exchange |

### Platform-specific notes

#### LinkedIn
- Scopes: `r_liteprofile`, `r_emailaddress`, `w_member_social`, `w_organization_social`
- Company Page posting requires `w_organization_social`
- Organizations are synced automatically after connect

#### Twitter/X
- PKCE required (S256)
- Scopes: `tweet.read`, `tweet.write`, `users.read`, `offline.access`
- Access token expires in 2 hours — refresh token is essential
- Confidential client (uses `client_secret_basic` auth)

#### Facebook
- Scopes: `public_profile`, `email`, `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`
- Callback exchanges short-lived token for long-lived (60 days)
- Page tokens are permanent — stored for posting
- First managed page is selected automatically

#### Instagram
- Uses Facebook Login flow (not Instagram Login)
- Scopes: `instagram_basic`, `instagram_content_publish`, `pages_show_list`
- Requires IG Business Account linked to a Facebook Page
- Callback discovers IG Business Account via page > instagram_business_account

#### Threads
- Scopes: `threads_basic`, `threads_content_publish`, `threads_manage_insights`
- Uses separate Threads App ID (not the main Meta App ID)
- Token exchange at `graph.threads.net/oauth/access_token`
- Long-lived token via `th_exchange_token` (60 days)

#### TikTok
- PKCE required (S256)
- Uses `client_key` instead of `client_id` (custom `TikTokOAuth2` class)
- Scopes: `user.info.basic`, `video.publish`, `video.upload` (comma-separated)
- Token exchange at `open.tiktokapis.com/v2/oauth/token/`
- `open_id` stored in account metadata

### Debugging OAuth failures

#### 1. Check the generated authorize URL

```bash
## Via the API
curl -X POST http://localhost:8083/api/v1/accounts/connect \
  -H "Authorization: Bearer {JWT}" \
  -H "Content-Type: application/json" \
  -d '{"platform": "twitter"}' | python3 -m json.tool
```

Verify:
- `client_id` is present and correct
- `redirect_uri` matches what's registered in the developer portal
- `scope` contains the right permissions
- `state` is present (CSRF protection)
- PKCE params (`code_challenge`, `code_challenge_method`) for Twitter/TikTok

#### 2. Check API logs

```bash
cd /home/tbaltzakis/cu130-slim
docker compose logs social-api --tail 50 | grep -i "oauth\|error\|callback"
```

#### 3. Check the callback

The callback at `/api/v1/auth/oauth/{platform}/callback` handles:
- Error responses from the platform
- State decoding (plain UUID or base64 JSON with PKCE verifier)
- Token exchange
- User info fetch
- Account storage

Common callback errors:
- `KeyError: 'access_token'` — token exchange returned an error, not a token
- `Token exchange failed` — platform rejected the code/client/redirect
- `No authorization code returned` — user denied consent

#### 4. Check env vars

```bash
cd /home/tbaltzakis/cu130-slim
grep -E "^(TWITTER|FACEBOOK|INSTAGRAM|THREADS|TIKTOK|LINKEDIN)_(CLIENT_ID|CLIENT_SECRET|REDIRECT_URI)" .env | awk -F= '{print $1": "length($2)" chars"}'
```

All should show non-zero char counts.

### TikTok scope upgrades (headless reconnect)

**Token refresh can only renew granted scopes — it can never add new ones.**
Granting a new scope (e.g. `user.info.stats`) requires a fresh authorize
round-trip. The consent screen grants any scope the client requests —
scopes do NOT need to be pre-enabled in the developer console (verified
2026-09-29: `user.info.stats` granted at consent while absent from the
app's Scopes list).

```bash
## Dry-run: dump the consent screen, don't click Continue
.devin/skills/social-accounts-manager/social-oauth-ops/scripts/tiktok-reconnect.py --dry-run

## Full reconnect (requires tiktok_web_cookies on the account — QR login
## in tiktok-console-ops, and the requested scopes in accounts.py's list)
.devin/skills/social-accounts-manager/social-oauth-ops/scripts/tiktok-reconnect.py
```

The script exports the stored `.tiktok.com` cookies, fetches a fresh
authorize URL (`/api/v1/accounts/connect` — state carries team + PKCE
verifier, fully stateless), drives the consent page headlessly, clicks
Continue, and prints the granted scopes after the callback.

### Scripts

- `scripts/check-all-accounts.py` — List all connected accounts and their status
- `scripts/refresh-tokens.py` — Trigger token refresh for all eligible accounts
- `scripts/tiktok-reconnect.py` — Cookie-driven TikTok re-consent for scope upgrades
- `scripts/tiktok-oauth.mjs` — Playwright consent driver (used by tiktok-reconnect.py)

## Social Profile Secrets

Store service and account credentials in the SocialAuto secret store
(`/api/v1/secrets`). The store uses **Cloudflare D1 as primary**, **local
PostgreSQL as failover**, and the local `.env` file as a final fallback.

### When to use

- Saving Instagram private API username/password
- Saving Facebook/LinkedIn browser automation credentials
- Saving Twitter/X v1.1 API key/secret and access tokens
- Saving TikTok private API signing key
- Listing, updating, or deleting saved secrets

### Supported secret keys

| Key | Platform | Used by |
|-----|----------|---------|
| `INSTAGRAM_USERNAME` | Instagram | `aiograpi-rest` sidecar |
| `INSTAGRAM_PASSWORD` | Instagram | `aiograpi-rest` sidecar |
| `FACEBOOK_USERNAME` | Facebook (personal) | Playwright browser login |
| `FACEBOOK_PASSWORD` | Facebook (personal) | Playwright browser login |
| `LINKEDIN_USERNAME` | LinkedIn (personal) | Playwright browser login |
| `LINKEDIN_PASSWORD` | LinkedIn (personal) | Playwright browser login |
| `TWITTER_API_KEY` | Twitter/X | `tweepy` v1.1 API |
| `TWITTER_API_SECRET` | Twitter/X | `tweepy` v1.1 API |
| `TWITTER_ACCESS_TOKEN` | Twitter/X | `tweepy` v1.1 API |
| `TWITTER_ACCESS_TOKEN_SECRET` | Twitter/X | `tweepy` v1.1 API |
| `TIKTOK_PRIVATE_API_KEY` | TikTok | `tiktok-private-api` signing server |

### Scripts

Run from repo root `cu130-slim/`:

```bash
## List saved secrets (values are masked)
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/list-secrets.py

## Save a single secret
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/set-secret.py INSTAGRAM_USERNAME cloudless_gr

## Get a raw secret value (admin/owner)
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/get-secret.py INSTAGRAM_USERNAME

## Save Instagram credentials
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/set-instagram.py cloudless_gr <password>

## Save Facebook browser credentials
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/set-facebook.py baltzakis.themis@gmail.com <password>

## Save LinkedIn browser credentials
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/set-linkedin.py user@example.com <password>

## Save Twitter v1.1 credentials
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/set-twitter.py key secret token token_secret

## Save TikTok private API key
.devin/skills/social-accounts-manager/social-profile-secrets/scripts/set-tiktok.py key
```

### API base

```
http://127.0.0.1:8083/api/v1/secrets
```

### Authentication

Bearer token from `POST /api/v1/auth/login`.

### Important notes

- The secret store is **Cloudflare-first**: when `CLOUDFLARE_ACCOUNT_ID`,
  `CLOUDFLARE_API_TOKEN`, and `D1_SOCIAL_AUTOMATION_ID` are configured,
  secrets are written to the D1 `social_secrets` table.
- If D1 is unavailable, the service writes to the local `social_secrets`
  PostgreSQL table.
- The `.env` file is read as a final fallback but is **read-only from the
  `social-api` container**. Update `.env` through the Env Manager
  (`http://localhost:8080`) if needed.
- Never print or commit secret values. Use the scripts to avoid typing
  credentials into conversation.
- After saving credentials, call the profile login endpoint for the
  corresponding account:
  ```bash
  curl -X POST http://127.0.0.1:8083/api/v1/profile/{account_id}/login \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
  ```
