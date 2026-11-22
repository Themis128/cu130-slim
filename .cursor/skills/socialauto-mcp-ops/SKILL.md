---
name: socialauto-mcp-ops
description: >-
  Operate the SocialAuto platform through its own MCP server and the repo-local
  MCP fleet — launch/verify each server in .devin/mcp_config.json, the 35-tool
  socialauto surface (accounts, posts, publishing, Messenger, Telegram), auth
  model, and fallbacks when MCP is unavailable. Use for "use the SocialAuto
  platform", MCP tooling questions, or when an MCP server fails to start.
---

# SocialAuto platform ops — MCP surface + verification

The platform exposes itself to agents two ways: the **socialauto MCP server**
(`app/mcp/server.py`, runs inside `social-api`) and the **REST API** it wraps.
All repo-local MCP servers are declared in `.devin/mcp_config.json`.

## Health check — one command

```bash
python3 .devin/skills/socialauto-mcp-server/scripts/socialauto_mcp_probe.py --all
python3 .devin/skills/socialauto-mcp-server/scripts/socialauto_mcp_probe.py socialauto n8n platform
```

Probes every server in `mcp_config.json`: spawns stdio servers, runs the
MCP `initialize` + `tools/list` handshake, and GET/TCP-checks URL endpoints.
Exit 0 = all healthy. Last full run: **13/20 OK**; expected fails are the
OAuth-gated mcp-remote wrappers (slack ×2, polar, cloudflare-api/docs) until
interactive auth completes once.

## socialauto MCP — launch

```json
"socialauto": {
  "command": "docker",
  "args": ["exec", "-i", "-e", "SOCIALAUTO_URL=http://social-api:8000",
           "social-api", "python3", "-m", "app.mcp.server"]
}
```

**Use `docker exec`, NOT `docker compose exec`** — `social-api` runs under the
`socialauto-standalone` compose project (`deploy/socialauto-standalone`), so
compose exec from the repo root reports "service not running" and the server
never starts (fixed in PR #2479).

Auth: the server mints/uses the platform session token internally
(`_get_token`, `_totp_code` for 2FA-gated flows); no token needed in config.

## Tool surface (35 tools, verified 2026-11-22)

| Group | Tools |
|---|---|
| Accounts | `list_accounts`, `get_account` |
| Content | `create_post`, `list_posts`, `publish_post`, `generate_content`, `suggest_hashtags`, `score_content` |
| Analytics | `get_analytics` |
| Media | `list_media` |
| Brand | `get_profile`, `get_brand` |
| Messenger (page) | `messenger_setup`, `messenger_get_profile`, `messenger_update_profile`, `messenger_send_message`, `messenger_list_conversations`, `messenger_get_messages`, `messenger_get_auto_reply`, `messenger_set_auto_reply`, `messenger_unsubscribe`, `messenger_list_all_accounts` |
| Messenger (personal) | `messenger_personal_conversations`, `messenger_personal_messages`, `messenger_personal_send`, `messenger_personal_get_auto_reply`, `messenger_personal_set_auto_reply` |
| Telegram | `telegram_list_channels`, `telegram_chat_info`, `telegram_chat_admins`, `telegram_member_count`, `telegram_set_chat_description`, `telegram_create_invite_link`, `telegram_send_message`, `telegram_pin_message` |

Source of truth: `social-automation/backend/app/mcp/server.py` — extend tools
there, not in ad-hoc scripts.

## Repo MCP fleet status (2026-11-22 audit)

| Server | Transport | Status |
|---|---|---|
| socialauto | stdio (docker exec) | ✅ 35 tools |
| n8n | stdio | ✅ 13 tools |
| platform | stdio | ✅ 8 tools (stack_ps, pg_query, celery_status…) |
| dmr | stdio | ✅ 26 tools |
| phone | stdio | ✅ 10 tools |
| tiktok-console | stdio | ✅ 9 tools |
| metabase | stdio (mcp-remote→CognitionAI server) | ✅ 19 tools |
| cloudflare | stdio | ✅ 8 tools |
| google-photos | stdio (npx) | ✅ 26 tools |
| linkedin-ads | stdio (uvx) | ✅ 11 tools |
| nextcloud | http :9231 | ✅ alive |
| deepwiki | http | ✅ alive |
| linkedin | http :9227 | ✅ alive (stack-ops publishes the socat forward) |
| airbyte | http :9228 | ✅ alive (stack-ops socat forward) |
| playwright | stdio (docker run) | ✅ 25 tools — needs `.playwright-data/pw-mcp-config.json` + `init-script.js` (gitignored; restore from git history `a94ff269f` if the dir is wiped) |
| slack, slack-cloudless | mcp-remote OAuth | ✅ 27 tools each — tokens cached in `~/.mcp-auth/<workspace>/` |
| polar | mcp-remote OAuth | ✅ 5 tools |
| cloudflare-api | mcp-remote OAuth | ✅ 5 tools |
| cloudflare-docs | mcp-remote OAuth | ✅ 2 tools |

**20/20 healthy** as of 2026-11-22. Two probe gotchas learned the hard way:
mcp-remote proxies take ~20-75s warmup (OAuth discovery + token refresh),
and they **exit on stdin EOF** — the probe keeps stdin open; MCP clients
must too.

### If an OAuth token ever expires

mcp-remote re-opens the browser automatically on next launch (refresh-token
failure → interactive consent). For unattended re-auth, the proven pattern
(github.com/Lifecycle-Innovations-Limited/claude-ops `ops-mcp-reauth.py`):
spawn the wrapper, scrape the `authorize?` URL from stderr, drive consent in
the :9229 CDP Chrome, mcp-remote writes `tokens.json` itself. mcp-remote also
supports `--device-code` (RFC 8628) for hosts without a browser.

## Fallback when MCP is down

- Platform REST: `http://social-api:8000` inside the compose network,
  `http://localhost:8083` from WSL (see `socialauto-publish` /
  `socialauto-media` skills for endpoint maps)
- DB direct: `platform` MCP `pg_query` tool, or `docker exec social-postgres psql`
- n8n: skill scripts under `.devin/skills/n8n-cloudless/scripts/` (fallback
  order per `n8n-mcp-first` rule: MCP → skill scripts → direct API)

## Gotchas

- `docker compose ps` from repo root shows the WRONG project for social-api —
  it's `deploy/socialauto-standalone`. Use `docker ps` / `docker exec`.
- Workers recreate in waves after merges (`master-deploy-watch`) — a probe
  FAIL during a recreate window is transient; re-run in ~60s.
- Never put tokens in `mcp_config.json`; wrappers source `.env` /
  `.devin/metabase-mcp.env` at runtime.
