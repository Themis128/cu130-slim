# Connecting social accounts

Connect one or more social platforms to a team. Each platform needs its own OAuth app, created outside SocialAuto.

## Before you start

1. You must be the **Owner** or an **Admin** of the team.
2. You need a developer account on each platform you want to connect.
3. Copy the OAuth **Client ID** and **Client Secret** into the Env Manager or `.env` file.

## 1. LinkedIn

1. Go to **Accounts > LinkedIn**.
2. Click **Connect LinkedIn**.
3. In the OAuth popup, log in and authorise the app.
4. Choose the **Company Page** you want to post to, or leave it as personal if that is what you need.
5. The account card turns green. The default Cloudless Company Page URN is pre-filled when you use the carousel flow.

## 2. X / Twitter

1. Go to **Accounts > X / Twitter**.
2. Click **Connect X**.
3. Authorise the app in the popup.
4. After redirect, the account appears in the list with its handle and scopes.

### Twitter/X OAuth requirements

- **OAuth 2.0 with PKCE** — SocialAuto generates a PKCE pair (`code_challenge` + `S256`) for every Twitter authorization request.
- **Scopes**: `tweet.read`, `tweet.write`, `users.read`, `offline.access` (for refresh token).
- **Redirect URI**: `https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback` (must be HTTPS, registered in the X Developer Console).
- **Client type**: Web App (confidential client) — uses `client_secret_basic` auth.
- **Token lifetime**: Access token expires in 2 hours; refresh token persists until revoked (requires `offline.access`).

Create your app at [console.x.com](https://console.x.com), enable OAuth 2.0, set the redirect URI, and copy the Client ID and Client Secret to `.env`.

## 3. TikTok

1. Go to **Accounts > TikTok**.
2. Click **Connect TikTok**.
3. Log in with a TikTok account and approve the scopes (`user.info.basic`, `video.publish`, `video.upload`).
4. After redirect, the account appears in the list with its username and avatar.

### TikTok OAuth requirements

TikTok's Login Kit has several non-standard OAuth requirements that SocialAuto handles automatically:

- **`client_key`** is used instead of `client_id` in both the authorize URL and token exchange.
- **PKCE** (`code_challenge` + `code_challenge_method=S256`) is required in the authorize URL.
- **Scopes** must be comma-separated (not space-separated like other platforms).
- **Redirect URI** must use `https://` and be registered exactly in the TikTok Developer Portal under Login Kit → Redirect URI → Web.

The production redirect URI is `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback`, routed through the Cloudflare named tunnel to the local `social-api` container. See the [TikTok content posting guide](10-tiktok-content-posting.md) for full OAuth troubleshooting.

## 4. Facebook

1. Go to **Accounts > Facebook**.
2. Click **Connect Facebook**.
3. Log in with a Facebook account that manages the cloudless.gr Page.
4. After redirect, the first managed Page is stored with its permanent Page token.

### Facebook OAuth requirements

- **Scopes**: `public_profile`, `email`, `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`.
- **Redirect URI**: `https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback` (must be HTTPS, registered in the Meta App Dashboard under Facebook Login > Settings).
- **Token lifecycle**: Short-lived user token (~1 hour) is exchanged for a long-lived token (~60 days). Page tokens are permanent.
- **App**: Uses the shared Meta app (App ID: `1936126137016578`).

## 5. Instagram

1. Go to **Accounts > Instagram**.
2. Click **Connect Instagram**.
3. Log in with a Facebook account that manages a Page linked to an Instagram Business account.
4. After redirect, the Instagram Business account is discovered and stored.

### Instagram OAuth requirements

- **Flow**: Uses Facebook Login (not Instagram Login) — the Instagram Business account is discovered via the Facebook Page's `instagram_business_account` field.
- **Scopes**: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`.
- **Redirect URI**: `https://social.cloudless.gr/api/v1/auth/oauth/instagram/callback` (must be HTTPS).
- **Requirement**: Instagram account must be a **Business or Creator** account linked to a Facebook Page. Personal accounts cannot publish via the API.
- **App**: Uses the shared Meta app (same as Facebook).

### Fixing the Instagram-Facebook Page connection

If no Instagram Business Account is found after connecting:
1. Go to `facebook.com/settings/?tab=linked_instagram`
2. Click "Review connection" next to the Instagram account
3. Enter the Instagram password and approve all permissions
4. If "Business Account Not Allowed to Advertise" appears — ignore it (that's about ads, not the API)
5. Reconnect Instagram in SocialAuto

## 6. Threads

1. Go to **Accounts > Threads**.
2. Click **Connect Threads**.
3. Log in with a Threads account and approve the scopes.
4. After redirect, the account appears with its username and profile picture.

### Threads OAuth requirements

- **Scopes**: `threads_basic`, `threads_content_publish`, `threads_manage_insights`.
- **Redirect URI**: `https://social.cloudless.gr/api/v1/auth/oauth/threads/callback` (must be HTTPS, registered in the Meta App Dashboard under Threads > Settings).
- **App ID**: Threads uses a **separate App ID and App Secret** from the main Meta app. Find them in App Dashboard > Settings > Basic > Threads App ID/Secret.
- **Token lifecycle**: Short-lived token (~1 hour) exchanged for long-lived (~60 days) via `th_exchange_token`. Refresh via `/refresh_access_token`.
- **Testers**: In development mode, add Threads testers in App Dashboard > App roles > Roles.

## 5. Verify connection health

1. In the **Accounts** list, each card shows:
   - **Status** (connected / expired)
   - **Scopes** granted
   - **Follower count** when available
2. Click the **Refresh** icon to re-sync an account.

## Troubleshooting

- **Token expired**: click **Reconnect** and repeat the OAuth flow.
- **Missing scope**: delete the connection and reconnect, making sure to approve every requested permission.
- **Company Page not listed**: ensure the LinkedIn app has the `w_organization_social` product and that you are an admin of the page.
- **TikTok `redirect_uri` error**: ensure `TIKTOK_REDIRECT_URI` uses `https://` and is registered exactly in the TikTok Developer Portal. The `http://localhost` URI is rejected by TikTok even for local testing — use the Cloudflare tunnel URL instead.
- **TikTok `scope` error**: scopes must be comma-separated. If you see this, ensure the latest `accounts.py` and `auth.py` are deployed (they pass scopes via `extras_params` with `",".join()`).
- **TikTok `code_challenge` error**: PKCE is required. Ensure the latest backend code is deployed — SocialAuto generates a PKCE pair for every TikTok authorization request.
- **TikTok `KeyError: access_token`**: the token exchange must use `client_key` (not `client_id`). The custom `TikTokOAuth2` class in `auth.py` handles this — ensure it's used instead of `BaseOAuth2`.
- **TikTok login rate limit**: if you see "Maximum number of attempts reached", wait 15–30 minutes before retrying.
