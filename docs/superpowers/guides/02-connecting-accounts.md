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

## 4. Facebook, Instagram, and Threads

1. Go to **Accounts > Meta**.
2. Click **Connect Facebook/Instagram/Threads**.
3. The Meta OAuth flow lets you select which Pages and Instagram accounts to authorise.
4. Each selected account appears as a separate card in SocialAuto.

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
