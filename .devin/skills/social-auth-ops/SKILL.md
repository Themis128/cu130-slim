# social-auth-ops

Recovery toolkit for broken/empty social-account credentials. One script
per gate; each prints exactly what the human must do and wires the rest.

## Gate map (from live diagnosis 2026-10-25)

| Gate | Root cause | Script |
|---|---|---|
| **X/Twitter** | ALL `TWITTER_*` env empty → connect URL had `client_id=` blank; publish fails 401 | `scripts/x-setup.sh` |
| **TikTok** | refresh_token dead (`no access_token in refresh response`) — TikTok refresh tokens expire at 365 days or on revoke; needs **full re-auth**, refresh can't recover | `scripts/tiktok-reconnect.sh` |
| **Email** | `EMAIL_PROVIDER=smtp` → smtp.resend.com with `SMTP_PASSWORD` empty (Resend SMTP wants the **API key** as password) | `scripts/email-setup.sh` |

## X/Twitter — new app credentials

1. developer.x.com → your project → App → Keys and tokens:
   - **OAuth 2.0 Client ID + Client Secret** (posting needs OAuth2)
   - API Key + Secret (v1.1 media upload fallback)
   - App permissions: **Read and write** (required before keygen)
   - Callback URI: `http://localhost:8083/api/v1/auth/oauth/twitter/callback`
     (or your prod domain equivalent — must match `TWITTER_REDIRECT_URI`)
   - Scopes needed: `tweet.read tweet.write users.read media.write
     offline.access dm.read dm.write`
2. Run: `scripts/x-setup.sh <client_id> <client_secret>`
   → writes `.env`, then prints the authorize URL. Open it, approve, done.
3. Re-queue the failed target: `scripts/requeue.sh <post_id> twitter`

## TikTok — full re-auth

Refresh tokens die at 365 days or on revoke — a dead refresh can't be
recovered, only re-authorized (developers.tiktok.com Login Kit).

- `scripts/tiktok-reconnect.sh` → admin-login → `POST /connect/tiktok` →
  prints the authorize URL. Open it, approve scopes
  (`user.info.basic video.publish video.upload`), done.
- Unaudited-app caveat: `video.publish` posts direct only after TikTok
  audits the app; until then uploads land in the user's TikTok inbox
  (MEDIA_UPLOAD fallback — works, but needs in-app confirm).
- If scopes need expanding later: use the QR-cookie flow in
  `social-oauth-ops/scripts/tiktok-reconnect.py` (see `tiktok-console-ops`).

## Email — pick one

- **Resend**: resend.com → API Keys → `re_…` →
  `scripts/email-setup.sh re_xxxxx` → sets `SMTP_PASSWORD`, restarts
  workers, sends a test to `reviewer@cloudless.gr`.
- **Cloudflare Email**: set `EMAIL_PROVIDER=cloudflare` +
  `CLOUDFLARE_EMAIL_API_TOKEN` in `.env` manually, same restart.

## Verify

`scripts/gate-check.sh` — prints each credential's live state
(set/empty, last refresh error, last publish result) so you can tell
fixed vs still-broken at a glance.
