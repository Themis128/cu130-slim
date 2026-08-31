# TikTok content posting

SocialAuto supports TikTok Upload Draft and Direct Post through the Content Posting API.

## Prerequisites

1. Add the **Content Posting API** product to the TikTok developer app.
2. Obtain approval for `video.upload` to use Upload Draft.
3. Obtain approval for `video.publish` to use Direct Post.
4. Reconnect the TikTok account after changing scopes so the access token includes the approved permissions.
5. Verify the HTTPS domain or URL prefix used by `MEDIA_PUBLIC_BASE_URL`. TikTok rejects `PULL_FROM_URL` media hosted outside a verified property.

## OAuth configuration

TikTok Login Kit has several non-standard OAuth requirements that SocialAuto handles automatically:

1. **`client_key` instead of `client_id`** — TikTok's authorize and token endpoints require `client_key` as the parameter name. SocialAuto's custom `TikTokOAuth2` class (`app/api/auth.py`) overrides `get_access_token` and `refresh_token` to send `client_key` in the token exchange. The authorize URL also includes `client_key` as an extra parameter alongside the standard `client_id`.

2. **PKCE (Proof Key for Code Exchange)** — TikTok requires `code_challenge` and `code_challenge_method=S256` in the authorize URL. SocialAuto generates a PKCE pair for every TikTok authorization request and encodes the `code_verifier` in the state parameter so the callback can use it during token exchange.

3. **Comma-separated scopes** — Unlike most OAuth providers that use space-separated scopes, TikTok requires scopes as a comma-separated string (e.g. `user.info.basic,video.publish,video.upload`). SocialAuto passes scopes via `extras_params` with `",".join(scopes)` and sets the library `scope` parameter to `None` to prevent the default space-joining behaviour.

4. **HTTPS-only redirect URIs** — TikTok rejects `http://` redirect URIs. The `TIKTOK_REDIRECT_URI` must use `https://` and must be registered exactly in the TikTok Developer Portal under Login Kit → Redirect URI → Web. The production callback is `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback`, routed through the Cloudflare named tunnel to the local `social-api` container.

### Required environment variables

```
TIKTOK_CLIENT_KEY=<from TikTok Developer Portal>
TIKTOK_CLIENT_SECRET=<from TikTok Developer Portal>
TIKTOK_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback
```

### TikTok Developer Portal setup

1. Create an app at [developers.tiktok.com](https://developers.tiktok.com).
2. Add **Login Kit** and **Content Posting API** products.
3. Under Login Kit → Redirect URI → Web, add: `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback`
4. Under Scopes, ensure `user.info.basic`, `video.publish`, and `video.upload` are added.
5. Enable **Direct Post** under Content Posting API if you want to use direct publishing.
6. Verify the domain `social.cloudless.gr` under Content Posting API → Verify domains (required for `PULL_FROM_URL`).
7. Create a sandbox version for testing with target users before going live.

## Upload a draft

1. Open **Content** and choose **New post**.
2. Select the TikTok account and add an MP4, MOV, WebM, or up to 35 photos.
3. Under **TikTok publishing**, select **Upload draft — finish in TikTok inbox**.
4. Publish or schedule the post.
5. Wait for the TikTok inbox notification.
6. Open TikTok, review the draft, finish editing, and publish it.

Upload Draft is the default. It uses `video.upload`. Video URLs are submitted to `/v2/post/publish/inbox/video/init/`; photos use `/v2/post/publish/content/init/` with `post_mode` set to `MEDIA_UPLOAD`.

## Publish directly

1. Confirm the developer app and connected account are approved for `video.publish`.
2. Open **Content** and choose **New post**.
3. Select TikTok and add media.
4. Under **TikTok publishing**, select **Direct publish**.
5. Select a privacy level offered by the creator account.
6. Publish or schedule the post.

SocialAuto queries `/v2/post/publish/creator_info/query/` before Direct Post and rejects privacy values not offered by TikTok. Videos use `/v2/post/publish/video/init/`; photos use `/v2/post/publish/content/init/` with `post_mode` set to `DIRECT_POST`.

## File and URL transfer

The normal publishing pipeline uses `PULL_FROM_URL`. The media URL must:

- use HTTPS;
- belong to a verified TikTok developer property;
- return the media without redirecting;
- remain accessible while TikTok downloads it.

The API client also supports `FILE_UPLOAD`. It initializes the upload with the byte size, chunk size, and chunk count, then uploads sequential chunks with `Content-Length`, `Content-Range`, and the correct video MIME type. Chunks follow TikTok's 5–64 MB limits, except that files smaller than 5 MB are uploaded whole.

## Statuses

SocialAuto polls `/v2/post/publish/status/fetch/` after initialization:

- `SEND_TO_USER_INBOX` means an Upload Draft is ready for review in TikTok.
- `PUBLISH_COMPLETE` means Direct Post completed, or the user completed an uploaded draft.
- `FAILED` or `CANCELLED` records the TikTok failure reason.
- Processing that exceeds the polling window is reported as a timeout while retaining the `publish_id` in the error.

## Troubleshooting OAuth errors

TikTok returns Greek-language error pages with a reference ID. Common causes and fixes:

| Error field | Cause | Fix |
|-------------|-------|-----|
| `client_key` (`errCode=10003`) | Authorize URL missing `client_key` param | SocialAuto adds it automatically; ensure `TIKTOK_CLIENT_KEY` is set in `.env` |
| `redirect_uri` | Redirect URI not registered in TikTok Developer Portal, or using `http://` instead of `https://` | Add the exact `https://` callback URL to Login Kit → Redirect URI → Web in the portal |
| `code_challenge` (`errCode=10007`) | PKCE not included in authorize URL | SocialAuto generates PKCE automatically for TikTok; ensure the latest `accounts.py` and `auth.py` are deployed |
| `scope` | Scopes not enabled in the sandbox/app, or sent as space-separated instead of comma-separated | Verify scopes are added in the Developer Portal; SocialAuto sends them comma-separated per TikTok docs |
| `KeyError: access_token` | Token exchange sent `client_id` instead of `client_key` | Custom `TikTokOAuth2` class handles this; ensure it's used instead of `BaseOAuth2` |
| Maximum login attempts | TikTok rate-limits repeated login attempts from the same IP/browser | Wait 15–30 minutes or use a different browser session |

### Unaudited client restriction

All content posted by unaudited TikTok clients is restricted to **private viewing mode**. To post publicly, the app must pass TikTok's audit. Until then, Direct Post and Upload Draft both work, but only the creator can see the content.
