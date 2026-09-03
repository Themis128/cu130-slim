---
name: tiktok-dev-console
description: >-
  Manage the TikTok developer console for the Cloudless app: domain verification
  for PULL_FROM_URL, app-to-organization transfer, audit submission for
  DIRECT_POST access, sandbox vs production mode, and URL properties setup.
  Use when verifying social.cloudless.gr for TikTok, transferring the app to
  an org, submitting for audit, or debugging url_ownership_unverified errors.
allowed-tools:
  - read
  - exec
  - grep
  - glob
  - web_search
  - webfetch
triggers:
  - user
  - model
---

# TikTok Developer Console

Manage the TikTok developer app configuration for SocialAuto's TikTok
integration.

## When to use

- Verify domain ownership for `PULL_FROM_URL` media transfers
- Transfer the Cloudless app from individual to organization ownership
- Submit the app for audit (required for `DIRECT_POST` mode)
- Check app status, scopes, and sandbox vs production mode
- Debug `url_ownership_unverified` errors
- Configure URL properties (domain or URL prefix)

## App details

| Field | Value |
|-------|-------|
| App name | Cloudless |
| App ID | 7630494700880906241 |
| Client key | `TIKTOK_CLIENT_KEY` in `.env` (sbawi6c3634oycojy9) |
| Client secret | `TIKTOK_CLIENT_SECRET` in `.env` |
| Current ownership | Individual (needs transfer to organization) |
| Redirect URI | `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback` |
| Products | Login Kit, Content Posting API |
| Mode | Sandbox (unaudited) |

## Organizations

| Org name | Org ID | Status |
|----------|--------|--------|
| cloudless.gr | 7630331010873377809 | Empty — no apps (target for transfer) |
| cloudless.gr | 7630331010873410577 | Empty — no apps |

Both orgs have the same display name. The app is currently under individual
ownership and needs to be transferred to one of them.

## Transfer app to organization

Per [TikTok docs](https://developers.tiktok.com/doc/working-with-organizations):

1. Go to **Manage apps** at https://developers.tiktok.com
2. Find the **Cloudless** app
3. Click the **three dots (...)** → **Transfer App**
4. Select organization `cloudless.gr` (Org ID: `7630331010873377809`)
5. Click **Initiate Transfer**
6. An email is sent to app administrators to accept the transfer
7. Accept the transfer — **this is irreversible**

After transfer, organization-level features become available, including
URL properties for domain verification.

## Domain verification for PULL_FROM_URL

TikTok's Content Posting API requires that any domain serving media via
`PULL_FROM_URL` be verified. Without verification, the API returns:

```
403 url_ownership_unverified
```

### Verification steps

Per [TikTok media transfer docs](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/#pull_from_url):

1. Log into https://developers.tiktok.com
2. Open the **Cloudless** app
3. Click **URL properties** button
4. Add a **Domain** property:
   - Enter `cloudless.gr` (base domain covers all subdomains including `social.cloudless.gr`)
   - Or enter `social.cloudless.gr` directly (subdomain only)
5. TikTok generates a DNS verification string (e.g. `tiktok-domain-verification=abc123...`)

### Add DNS TXT record in Cloudflare

1. Log into https://dash.cloudflare.com → select `cloudless.gr`
2. **DNS** → **Records** → **Add record**
3. Set:
   - Type: `TXT`
   - Name: `@` (for base domain) or `social` (for subdomain)
   - Content: the verification string from TikTok
4. Save — Cloudflare propagates within seconds

### Complete verification

1. Back in TikTok developer console, click **Verify**
2. TikTok checks the DNS record — should pass within a minute
3. All URLs under the verified domain are now trusted for `PULL_FROM_URL`

### Verification rules

- **Domain** verification covers all paths under that domain AND its subdomains
  - Verifying `cloudless.gr` covers `social.cloudless.gr`, `www.cloudless.gr`, etc.
- **URL Prefix** verification covers only URLs with the exact prefix
  - `https://example.com/videos/user/` covers `.../user/123.mp4` but not `.../2023/user/123.mp4`
- The media URL must use `https` and must not redirect (no 3xx)
- The URL must remain accessible for the entire download duration (1h timeout)
- Domain verification is recommended over URL prefix (broader coverage)

### Check if verification is needed

Only apps created after TikTok's enforcement date require URL verification.
Older apps may be grandfathered. Check by attempting a `PULL_FROM_URL` call —
if it returns `url_ownership_unverified`, verification is required.

## Workaround: FILE_UPLOAD (no verification needed)

For **video** posts, SocialAuto supports `FILE_UPLOAD` which reads the local
video file and uploads bytes directly to TikTok. This bypasses domain
verification entirely. See the `tiktok-publish` skill for details.

For **photo** posts, `PULL_FROM_URL` is the only option — domain verification
is mandatory.

## App audit for DIRECT_POST

`DIRECT_POST` mode (posting directly to the profile without creator approval)
requires the app to pass TikTok's audit review.

### Submit for audit

1. Open the **Cloudless** app in the developer console
2. Ensure all required fields are filled:
   - App name, description, logo
   - Privacy policy URL
   - Terms of service URL
   - Developer website
3. Click **Submit for review**
4. TikTok reviews the app (can take several business days)
5. Once approved, `DIRECT_POST` mode becomes available

### Before audit

Use `MEDIA_UPLOAD` mode only. The video goes to the creator's TikTok inbox
for manual posting. `DIRECT_POST` returns:

```
403 unaudited_client_can_only_post_to_private_accounts
```

## Sandbox mode

The app is in **Sandbox mode** — only sandbox users can authorize and use
the app. The sandbox user is `cloudless-dev` (target: `user3113682023385`).

To add sandbox users:
1. Open the app → **Sandbox** tab
2. Add TikTok usernames to the sandbox user list

To move to production:
1. Submit the app for review
2. Once approved, the app moves to Production mode
3. Any TikTok user can authorize the app

## TikTok OAuth specifics

TikTok Login Kit has several non-standard OAuth requirements:

- **`client_key`** (not `client_id`): Used in authorize URL and token exchange
- **PKCE required**: `code_challenge` + `code_challenge_method=S256` always
- **Comma-separated scopes**: `user.info.basic,video.publish,video.upload` (not space-separated)
- **HTTPS-only redirect URIs**: `TIKTOK_REDIRECT_URI` must use `https://`
- **24-hour tokens**: No `expires_in` on refresh — 24h assumed

The custom `TikTokOAuth2` class in `app/api/auth.py` handles all of these.

### Required scopes

| Scope | Purpose |
|-------|---------|
| `user.info.basic` | Read user profile info |
| `video.publish` | Publish content to TikTok |
| `video.upload` | Upload videos via Content Posting API |

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Check TikTok app configuration from .env
.devin/skills/tiktok-dev-console/scripts/check-app-config.sh

# Verify that social.cloudless.gr is reachable and serving media
.devin/skills/tiktok-dev-console/scripts/verify-media-url.sh

# Fetch the latest TikTok Content Posting API docs
.devin/skills/tiktok-dev-console/scripts/fetch-docs.sh
```

## Important notes

- Domain verification is a **one-time** setup per domain
- The app transfer to an organization is **irreversible**
- Sandbox mode restricts access to listed users only
- `MEDIA_UPLOAD` sends to inbox; `DIRECT_POST` publishes directly (needs audit)
- Never commit `TIKTOK_CLIENT_SECRET` or access tokens
- The TikTok developer console cannot be automated via API — all console
  operations (domain verification, app transfer, audit submission) are manual
