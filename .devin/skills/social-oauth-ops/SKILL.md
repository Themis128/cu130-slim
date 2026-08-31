# Social OAuth Operations

Day-to-day OAuth operations for all SocialAuto platforms: connect, reconnect,
refresh tokens, debug errors, and verify account health.
Use when connecting accounts, debugging OAuth failures, refreshing expired tokens,
or checking which accounts are connected.

## Supported platforms

| Platform | OAuth flow | PKCE | Token lifetime | Refresh |
|----------|-----------|------|----------------|---------|
| LinkedIn | OAuth 2.0 | No | 60 days | Auto via refresh_token |
| Twitter/X | OAuth 2.0 + PKCE | Yes (S256) | 2 hours | Via refresh_token (offline.access) |
| Facebook | OAuth 2.0 | No | 60 days (user), permanent (page) | Re-exchange |
| Instagram | OAuth 2.0 (via FB) | No | 60 days | Re-exchange |
| Threads | OAuth 2.0 | No | 60 days | Via /refresh_access_token |
| TikTok | OAuth 2.0 + PKCE | Yes (S256) | 24 hours | Via refresh_token |

## Auto token refresh

A Celery beat task `app.worker.tasks.token_refresh.refresh_expiring_tokens` runs every hour at :15 past the hour. It automatically refreshes any active account token expiring within the next 4 hours:

- **TikTok**: 24h tokens — refreshed daily (TikTok doesn't return `expires_in` on refresh, so 24h is assumed).
- **Twitter/X**: 2h tokens — refreshed every hour (requires `offline.access`).
- **Meta (FB/IG/Threads)**: ~60-day tokens — refreshed when within 4h of expiry.
- **LinkedIn**: tokens don't expire (no `expires_in`).

If a refresh fails, the account is marked as `expired` and requires manual reconnect from the Accounts page.

Trigger manually:
```bash
docker compose exec -T social-worker celery -A app.worker.celery_app call app.worker.tasks.token_refresh.refresh_expiring_tokens
```

## Common operations

### Connect an account

```bash
# Via the API (body-based endpoint)
curl -X POST http://localhost:8083/api/v1/accounts/connect \
  -H "Authorization: Bearer {JWT}" \
  -H "Content-Type: application/json" \
  -d '{"platform": "twitter"}'
# Returns: {"authorization_url": "https://..."}

# Via the API (path-based endpoint)
curl -X POST "http://localhost:8083/api/v1/accounts/connect/twitter?team_id={TEAM_ID}" \
  -H "Authorization: Bearer {JWT}"
```

Or use the frontend at http://localhost:8082/accounts.

### List connected accounts

```bash
curl http://localhost:8083/api/v1/accounts \
  -H "Authorization: Bearer {JWT}"
```

### Check account health

```bash
curl http://localhost:8083/api/v1/accounts/{account_id}/health \
  -H "Authorization: Bearer {JWT}"
```

### Reconnect an expired account

1. Delete the old connection:
   ```bash
   curl -X DELETE http://localhost:8083/api/v1/accounts/{account_id} \
     -H "Authorization: Bearer {JWT}"
   ```
2. Reconnect via the Connect button or API.

### Verify OAuth URL for a platform

```bash
# Meta platforms
bash .devin/skills/meta-oauth-setup/scripts/verify-oauth-urls.sh

# Twitter/X
bash .devin/skills/twitter-oauth-setup/scripts/verify-oauth-url.sh
```

## Backend OAuth endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/auth/oauth/{platform}` | GET | Generate authorize URL (general) |
| `/api/v1/accounts/connect` | POST | Generate authorize URL (body-based) |
| `/api/v1/accounts/connect/{platform}` | POST | Generate authorize URL (path-based) |
| `/api/v1/auth/oauth/{platform}/callback` | GET | OAuth callback + token exchange |

## Platform-specific notes

### LinkedIn
- Scopes: `r_liteprofile`, `r_emailaddress`, `w_member_social`, `w_organization_social`
- Company Page posting requires `w_organization_social`
- Organizations are synced automatically after connect

### Twitter/X
- PKCE required (S256)
- Scopes: `tweet.read`, `tweet.write`, `users.read`, `offline.access`
- Access token expires in 2 hours — refresh token is essential
- Confidential client (uses `client_secret_basic` auth)

### Facebook
- Scopes: `public_profile`, `email`, `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`
- Callback exchanges short-lived token for long-lived (60 days)
- Page tokens are permanent — stored for posting
- First managed page is selected automatically

### Instagram
- Uses Facebook Login flow (not Instagram Login)
- Scopes: `instagram_basic`, `instagram_content_publish`, `pages_show_list`
- Requires IG Business Account linked to a Facebook Page
- Callback discovers IG Business Account via page > instagram_business_account

### Threads
- Scopes: `threads_basic`, `threads_content_publish`, `threads_manage_insights`
- Uses separate Threads App ID (not the main Meta App ID)
- Token exchange at `graph.threads.net/oauth/access_token`
- Long-lived token via `th_exchange_token` (60 days)

### TikTok
- PKCE required (S256)
- Uses `client_key` instead of `client_id` (custom `TikTokOAuth2` class)
- Scopes: `user.info.basic`, `video.publish`, `video.upload` (comma-separated)
- Token exchange at `open.tiktokapis.com/v2/oauth/token/`
- `open_id` stored in account metadata

## Debugging OAuth failures

### 1. Check the generated authorize URL

```bash
# Via the API
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

### 2. Check API logs

```bash
cd /home/tbaltzakis/cu130-slim
docker compose logs social-api --tail 50 | grep -i "oauth\|error\|callback"
```

### 3. Check the callback

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

### 4. Check env vars

```bash
cd /home/tbaltzakis/cu130-slim
grep -E "^(TWITTER|FACEBOOK|INSTAGRAM|THREADS|TIKTOK|LINKEDIN)_(CLIENT_ID|CLIENT_SECRET|REDIRECT_URI)" .env | awk -F= '{print $1": "length($2)" chars"}'
```

All should show non-zero char counts.

## Scripts

- `scripts/check-all-accounts.sh` — List all connected accounts and their status
- `scripts/refresh-tokens.sh` — Trigger token refresh for all eligible accounts
