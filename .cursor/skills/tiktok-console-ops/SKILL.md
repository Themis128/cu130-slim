---
name: tiktok-console-ops
description: >-
  Operates TikTok for Developers console and SocialAuto TikTok runtime per
  official Content Posting / Login Kit docs: domain verification (PULL_FROM_URL),
  DNS TXT via Cloudflare, browser sidecar session, app audit readiness, and
  config drift checks (redirect URI, scopes, MEDIA_UPLOAD vs DIRECT_POST).
  Use when the user mentions TikTok console, url_ownership_unverified, domain
  verify, app audit, DIRECT_POST, sidecar session, or TikTok MCP tools.
---

# TikTok console ops

Official docs (Context7 `/websites/developers_tiktok` or developers.tiktok.com):

- Content Posting get-started / media transfer — `PULL_FROM_URL` needs verified domain
- Photo posts require `PULL_FROM_URL` (domain verify mandatory)
- Videos can use `FILE_UPLOAD` (no domain verify) or `PULL_FROM_URL`
- `DIRECT_POST` needs approved app audit; until then use `MEDIA_UPLOAD`
- Login Kit: `client_key`, PKCE S256, comma-separated scopes, HTTPS redirect only

## Cloudless defaults

| Field | Expected |
|-------|----------|
| App name / ID | Cloudless / `7630494700880906241` |
| Redirect URI | `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback` |
| Web / media domain | `cloudless.gr` (covers `social.cloudless.gr`) |
| Connected account | sandbox `user3113682023385` / brand cloudless.gr |
| Publish mode (pre-audit) | `MEDIA_UPLOAD` |
| Sidecar | `http://127.0.0.1:9224` |

**Drift to fix if seen in console:** `social.cloudless.jp` web URL or redirect — replace with `.gr` SocialAuto paths above.

## Env (never print secrets)

- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`
- `TIKTOK_DEV_EMAIL`, `TIKTOK_DEV_PASSWORD` — developer portal login
- `CLOUDFLARE_API_TOKEN` — DNS TXT for `tiktok-domain-verification=…`
- Site TXT `tiktok-developers-site-verification=…` is **not** Content Posting domain verify

## Tool scripts (repo root)

```bash
.cursor/skills/tiktok-console-ops/scripts/check-config.sh
.cursor/skills/tiktok-console-ops/scripts/sidecar-session.sh ensure   # Playwright Docker → POST /session
.cursor/skills/tiktok-console-ops/scripts/sidecar-session.sh status
.cursor/skills/tiktok-console-ops/scripts/console-inspect.sh          # login + dump app state
.cursor/skills/tiktok-console-ops/scripts/domain-verify.sh            # console token → CF TXT → Verify
.cursor/skills/tiktok-console-ops/scripts/dns-tiktok-txt.sh list|add <token>
```

## MCP server

`tiktok-console` in `.devin/mcp_config.json` →  
`.cursor/skills/tiktok-console-ops/scripts/tiktok-console-mcp-server.py`

Tools: `tiktok_check_config`, `tiktok_sidecar_status`, `tiktok_sidecar_ensure_session`,
`tiktok_dns_list`, `tiktok_dns_add_domain_txt`, `tiktok_console_inspect`,
`tiktok_domain_verify`, `tiktok_api_smoke`, `tiktok_docs_checklist`.

## Agent workflow

1. `check-config.sh` / `tiktok_check_config` — fix `.env` redirect/scopes drift first
2. `sidecar-session.sh ensure` if privacy/browser APIs needed
3. `domain-verify.sh` for photo `PULL_FROM_URL` / Direct Post domain gate
4. Do **not** submit app audit unless user explicitly asks; report readiness only
5. Prefer Playwright Docker (`mcr.microsoft.com/playwright:v1.62.1`) for console; dismiss cookie banner before clicks
6. After console URL/redirect edits, re-run SocialAuto OAuth reconnect if scopes/URI changed

## Related

- `.devin/skills/tiktok-dev-console/` — app/org/audit reference
- `.devin/skills/tiktok-publish/` — FILE_UPLOAD / spam / publish modes
- Playwright Docker MCP already in `.devin/mcp_config.json` (`playwright`)
