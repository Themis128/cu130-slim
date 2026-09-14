# TikTok console ops — reference

## Official docs

| Topic | URL |
|-------|-----|
| Media transfer / PULL_FROM_URL | https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/#pull_from_url |
| Content Posting get started | https://developers.tiktok.com/doc/content-posting-api-get-started |
| Photo post | https://developers.tiktok.com/doc/content-posting-api-reference-photo-post |
| Login Kit web | https://developers.tiktok.com/doc/login-kit-web |
| Set up development / URL ownership | https://developers.tiktok.com/doc/set-up-development-configuration |

Context7 library IDs: `/websites/developers_tiktok`, `/websites/developers_tiktok_en`

## OAuth (Login Kit)

- Param name: `client_key` (not `client_id`)
- PKCE: `code_challenge_method=S256`
- Scopes: comma-separated
- Redirect: HTTPS only; must match console + `.env` exactly

SocialAuto expected redirect:

`https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback`

Scopes to grant: `user.info.basic,user.info.profile,video.list,video.publish,video.upload`

## Domain verification

1. Console → Cloudless → Content Posting → **Verify domains** / URL properties
2. Add domain `cloudless.gr`
3. DNS TXT `@` = `tiktok-domain-verification=…` (Cloudflare)
4. Click **Verify**

`tiktok-developers-site-verification` ≠ Content Posting domain verify.

## Publish modes

| Mode | When |
|------|------|
| `MEDIA_UPLOAD` | Default until app audit approved |
| `DIRECT_POST` | After production audit approval |
| `FILE_UPLOAD` | Videos without domain verify |
| `PULL_FROM_URL` | Photos (required) + optional videos; needs domain verify |

## Live console drift (2026-09)

Observed on Cloudless app (`7630494700880906241`):

- Ownership: org **cloudless.gr** (transfer done)
- Production status: **Not approved**
- Risk: Web URL / redirect showing `social.cloudless.jp` — must be `.gr` SocialAuto paths
- Direct Post toggle may be ON while unaudited — SocialAuto must still prefer `MEDIA_UPLOAD`

## MCP tools

See `scripts/tiktok-console-mcp-server.py` and `.devin/mcp_config.json` entry `tiktok-console`.
