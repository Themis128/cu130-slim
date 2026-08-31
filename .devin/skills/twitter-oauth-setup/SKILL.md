# Twitter/X OAuth Setup

Set up OAuth 2.0 with PKCE for Twitter/X in SocialAuto.
Use when configuring Twitter/X OAuth, creating a developer app at console.x.com,
registering redirect URIs, or debugging Twitter OAuth errors.

## Architecture

Twitter/X uses **OAuth 2.0 Authorization Code Flow with PKCE** (Proof Key for Code Exchange).

| Component | Value |
|-----------|-------|
| Authorize URL | `https://twitter.com/i/oauth2/authorize` |
| Token URL | `https://api.twitter.com/2/oauth2/token` |
| User info URL | `https://api.twitter.com/2/users/me` |
| Auth method | `client_secret_basic` (confidential client) |
| PKCE | Required (`S256`) |

## Required scopes

SocialAuto requests these scopes:

| Scope | Purpose |
|-------|---------|
| `tweet.read` | Read tweets |
| `tweet.write` | Post tweets and retweets |
| `users.read` | Read user profile |
| `offline.access` | Get refresh token for long-lived access |

Optional scopes (add if needed):
- `like.read` / `like.write` — Like/unlike tweets
- `follows.read` / `follows.write` — Follow/unfollow
- `bookmark.read` / `bookmark.write` — Bookmarks
- `dm.read` / `dm.write` — Direct messages
- `list.read` / `list.write` — Lists
- `media.write` — Upload media

## Setup steps

### 1. Create a developer account

1. Go to https://console.x.com
2. Sign in with your X account
3. Accept the Developer Agreement
4. Complete your profile

### 2. Create an app

1. Click "New App" (or use an existing one)
2. Enter app name, description, and use case
3. Generate credentials

### 3. Configure OAuth 2.0

1. In the Developer Console, go to your app > Settings > Authentication
2. Enable OAuth 2.0
3. Select **Web App** (confidential client) to get a Client Secret
4. Set the redirect URI:
   ```
   https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback
   ```
5. Save settings

### 4. Save credentials

From the Developer Console > your app > Keys and Tokens:
- Copy the **OAuth 2.0 Client ID**
- Copy the **OAuth 2.0 Client Secret**
- (Optional) Copy the **Bearer Token** for app-only read access

### 5. Update `.env`

```bash
TWITTER_CLIENT_ID=<your_client_id>
TWITTER_CLIENT_SECRET=<your_client_secret>
TWITTER_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback
# Optional: for app-only read access
TWITTER_BEARER_TOKEN=<your_bearer_token>
```

### 6. Restart social-api

```bash
cd /home/tbaltzakis/cu130-slim
docker compose restart social-api
curl http://localhost:8083/health
```

### 7. Connect the account

Open http://localhost:8082/accounts and click **Connect X / Twitter**.

## Token lifecycle

| Token type | Validity | Refresh |
|------------|----------|---------|
| Access token | 2 hours | Use refresh token |
| Refresh token | Until revoked | Requires `offline.access` scope |

### Refresh token flow

```bash
POST https://api.twitter.com/2/oauth2/token
Content-Type: application/x-www-form-urlencoded

refresh_token={REFRESH_TOKEN}
&grant_type=refresh_token
&client_id={CLIENT_ID}
```

For confidential clients, include `Authorization: Basic base64(client_id:client_secret)` header.

## PKCE flow

SocialAuto generates PKCE automatically for Twitter:

1. Generate `code_verifier` (random URL-safe string, 43-128 chars)
2. Derive `code_challenge` = base64url(sha256(code_verifier))
3. Send `code_challenge` + `code_challenge_method=S256` in authorize URL
4. Send `code_verifier` in token exchange
5. Twitter verifies the challenge matches

The `code_verifier` is encoded in the OAuth state parameter (base64 JSON) so the callback can use it.

## App settings in Developer Console

| Setting | Value |
|---------|-------|
| App type | Web App (confidential client) |
| OAuth 2.0 | Enabled |
| Redirect URI | `https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback` |
| Scopes | `tweet.read`, `tweet.write`, `users.read`, `offline.access` |

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `redirect_uri` mismatch | URI not registered in Developer Console | Add the exact HTTPS redirect URI |
| `invalid_grant` | Code already used or expired | Re-authorize (codes expire in 30s) |
| `invalid_client` | Wrong Client ID/Secret | Check `.env` values |
| `PKCE verification failed` | Code verifier doesn't match challenge | Ensure PKCE pair is generated correctly |
| 403 Forbidden | App doesn't have access to endpoint | Check app permissions in Developer Console |
| 429 Too Many Requests | Rate limit hit | Check `x-rate-limit-reset` header |

## API endpoints used by SocialAuto

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `https://api.twitter.com/2/users/me` | GET | Get authenticated user's profile |
| `https://api.twitter.com/2/tweets` | POST | Create a tweet |
| `https://api.twitter.com/2/tweets/{id}` | DELETE | Delete a tweet |
| `https://api.twitter.com/2/users/{id}/tweets` | GET | List user's tweets |

## Scripts

- `scripts/verify-oauth-url.sh` — Generate and verify the Twitter OAuth authorize URL
