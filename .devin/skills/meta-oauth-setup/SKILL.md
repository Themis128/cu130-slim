# Meta OAuth Setup (Facebook + Instagram + Threads)

Set up OAuth for all three Meta platforms using a single Meta developer app.
Use when configuring Facebook, Instagram, or Threads OAuth in SocialAuto,
registering redirect URIs, retrieving Threads App ID/Secret, or debugging
Meta OAuth errors.

## Architecture

All three platforms share one Meta app but have **different OAuth endpoints and credentials**:

| Platform | Authorize URL | Token URL | App ID used |
|-----------|--------------|-----------|-------------|
| Facebook | `https://www.facebook.com/dialog/oauth` | `https://graph.facebook.com/oauth/access_token` | Main Meta App ID |
| Instagram (FB Login) | `https://www.facebook.com/dialog/oauth` | `https://graph.facebook.com/oauth/access_token` | Main Meta App ID |
| Threads | `https://threads.com/oauth/authorize` | `https://graph.threads.net/oauth/access_token` | **Threads App ID** (separate) |

**Key**: Threads has its own App ID and App Secret, found in App Dashboard > Settings > Basic > Threads App ID/Secret. Facebook and Instagram share the main Meta App ID and Secret.

## Existing Meta app

- **App ID**: `1936126137016578`
- **App Secret**: (in `.env` as `FACEBOOK_CLIENT_SECRET` / `INSTAGRAM_CLIENT_SECRET`)
- **App Dashboard**: https://developers.facebook.com/apps/1936126137016578
- **Facebook Page**: `116436681562585` (cloudless.gr)
- **Meta Business Portfolio**: `1558125105019725`

## Required redirect URIs

All redirect URIs must use **HTTPS** (Meta rejects `http://` except for localhost testing in development mode). Register these in the App Dashboard:

```
https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback
https://social.cloudless.gr/api/v1/auth/oauth/instagram/callback
https://social.cloudless.gr/api/v1/auth/oauth/threads/callback
```

For local development testing (only works if app is in development mode):
```
http://localhost:8083/api/v1/auth/oauth/facebook/callback
http://localhost:8083/api/v1/auth/oauth/instagram/callback
http://localhost:8083/api/v1/auth/oauth/threads/callback
```

## Required scopes

### Facebook (Pages API)
```
public_profile, email, pages_show_list, pages_read_engagement, pages_manage_posts
```

### Instagram (via Facebook Login flow)
```
instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, pages_manage_posts
```

**Note**: If using Business Login for Instagram (Instagram credentials, not Facebook), the scopes change to:
```
instagram_business_basic, instagram_business_content_publish
```
And the authorize URL changes to `https://www.instagram.com/oauth/authorize` with a separate Instagram App ID.
SocialAuto currently uses the Facebook Login flow.

### Threads
```
threads_basic, threads_content_publish, threads_manage_insights, threads_manage_replies
```

Optional Threads scopes (add if needed):
- `threads_read_replies` — for reading replies
- `threads_delete` — for deleting posts
- `threads_keyword_search` — for keyword search
- `threads_location_tagging` — for location tags
- `threads_manage_mentions` — for mention management
- `threads_profile_discovery` — for profile discovery

## Setup steps

### 1. Configure the Meta app

1. Go to https://developers.facebook.com/apps/1936126137016578
2. Ensure these use cases are added:
   - "Manage everything on your Page" (for Facebook + Instagram)
   - "Access the Threads API" (for Threads)
3. Under each use case, add the required permissions listed above.

### 2. Register redirect URIs

**For Facebook + Instagram** (Facebook Login settings):
1. App Dashboard > Use cases > Customize > Facebook Login > Settings
2. Add all three Facebook/Instagram redirect URIs to "Valid OAuth Redirect URIs"
3. Save

**For Threads** (separate settings):
1. App Dashboard > Use cases > Customize > Access the Threads API > Settings
2. Add the Threads redirect URI to "Client OAuth Settings"
3. Save

### 3. Get Threads App ID and Secret

1. App Dashboard > Settings > Basic
2. Scroll to find "Threads App ID" and "Threads App Secret"
3. These are **different** from the main Meta App ID/Secret

### 4. Add Threads testers

1. App Dashboard > App roles > Roles > Add People
2. Select "Threads Tester" role
3. The invited user must accept at threads.com/settings/account > Website permissions

### 5. Update `.env`

```bash
# Facebook (main Meta app credentials)
FACEBOOK_CLIENT_ID=1936126137016578
FACEBOOK_CLIENT_SECRET=<main_meta_app_secret>
FACEBOOK_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback

# Instagram (same main Meta app credentials)
INSTAGRAM_CLIENT_ID=1936126137016578
INSTAGRAM_CLIENT_SECRET=<main_meta_app_secret>
INSTAGRAM_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/instagram/callback

# Threads (separate Threads App ID/Secret)
THREADS_CLIENT_ID=<threads_app_id>
THREADS_CLIENT_SECRET=<threads_app_secret>
THREADS_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/threads/callback
```

### 6. Restart social-api

```bash
cd /home/tbaltzakis/cu130-slim
docker compose restart social-api
curl http://localhost:8083/health
```

### 7. Connect accounts

Open http://localhost:8082/accounts and click Connect for each platform.

## Token lifecycle

| Platform | Short-lived | Long-lived | Refresh |
|----------|-------------|------------|---------|
| Facebook | ~1 hour | ~60 days (user token) | Re-exchange before expiry |
| Facebook Page | — | Permanent (page token) | No refresh needed |
| Instagram | ~1 hour | ~60 days (via FB exchange) | Re-exchange before expiry |
| Threads | ~1 hour | ~60 days | `GET /refresh_access_token` |

### Facebook long-lived token exchange

```bash
GET https://graph.facebook.com/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id={APP_ID}
  &client_secret={APP_SECRET}
  &fb_exchange_token={SHORT_LIVED_TOKEN}
```

### Threads long-lived token exchange

```bash
GET https://graph.threads.net/access_token
  ?grant_type=th_exchange_token
  &client_secret={THREADS_APP_SECRET}
  &access_token={SHORT_LIVED_TOKEN}
```

### Threads token refresh

```bash
GET https://graph.threads.net/refresh_access_token
  ?grant_type=th_refresh_token
  &access_token={LONG_LIVED_TOKEN}
```

## Instagram Business Account requirement

Instagram publishing requires a **Business or Creator account** linked to a Facebook Page. The callback handler:
1. Exchanges the short-lived FB token for a long-lived token
2. Fetches `/me/accounts` (Facebook Pages)
3. Looks for `instagram_business_account` on each page
4. Falls back to `page_backed_instagram_accounts`
5. Falls back to Business Manager `instagram_accounts`

If no IG Business Account is found, the FB user info is stored as a fallback (posting won't work until the IG-FB Page link is established).

### Fixing the IG-FB Page connection

1. Go to `facebook.com/settings/?tab=linked_instagram`
2. Click "Review connection" next to the cloudless.gr Instagram account
3. Enter the Instagram password
4. Approve all permissions
5. If "Business Account Not Allowed to Advertise" appears — ignore it (that's about ads, not the API)

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `redirect_uri` mismatch | URI not registered or trailing slash mismatch | Check App Dashboard settings exactly match `.env` |
| `Invalid scope` | Scope not added to the app | Add the permission under the use case in App Dashboard |
| `Instagram Business Account not found` | IG account not linked to FB Page or is Personal | Switch IG to Business, complete "Review connection" |
| Threads `invalid_client_id` | Using main Meta App ID instead of Threads App ID | Use the Threads-specific App ID from Settings > Basic |
| `code 190` | Token expired | Re-connect the account |
| `App not in development mode` for localhost | App is live | Use the production HTTPS redirect URI through the Cloudflare tunnel |

## Scripts

- `scripts/verify-oauth-urls.sh` — Generate and verify OAuth authorize URLs for all three Meta platforms
- `scripts/check-meta-token.sh` — Check if a Meta access token is valid and show its expiry
