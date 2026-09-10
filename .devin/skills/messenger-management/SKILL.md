---
name: messenger-management
description: >-
  Manage ALL Facebook Messenger accounts (Pages + personal) through SocialAuto.
  Page Messenger uses the official Messenger Platform API (Graph API).
  Personal Messenger uses the browser bridge (noVNC + facebook.com/messages).
  List all accounts, send/receive messages, configure AI auto-reply, manage
  webhooks, and use the MCP server tools. Use when managing Messenger across
  multiple Facebook accounts, sending DMs, reading conversations, or
  debugging Messenger issues.
allowed-tools:
  - read
  - exec
  - grep
  - glob
  - web_search
  - webfetch
triggers:
  - user
  - model
---

# Messenger Management

Manage ALL Facebook Messenger accounts — both Facebook Pages (via official
Messenger Platform API) and personal accounts (via browser bridge).

## When to use

- List all Messenger-capable accounts (Pages + personal)
- Send/receive messages on any Messenger account
- Set up Messenger on a new Facebook Page
- Configure AI auto-reply for Page Messenger
- Read personal Messenger conversations (browser bridge)
- Send personal Messenger messages (browser bridge)
- Debug Messenger webhook issues
- Manage multiple Facebook Pages' Messenger from one place

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SocialAuto API                        │
│                                                          │
│  ┌─────────────────┐    ┌──────────────────────────┐    │
│  │  Page Messenger  │    │  Personal Messenger       │    │
│  │  (Graph API)    │    │  (Browser Bridge)         │    │
│  │                 │    │                           │    │
│  │  • Send API     │    │  • facebook.com/messages  │    │
│  │  • Profile API  │    │  • noVNC browser           │    │
│  │  • Webhooks     │    │  • Playwright automation   │    │
│  │  • Conversations│    │  • Cookie-based session    │    │
│  │  • AI auto-reply│    │  • No API key needed      │    │
│  └────────┬────────┘    └───────────┬──────────────┘    │
│           │                         │                    │
│           └──────────┬──────────────┘                    │
│                      │                                    │
│              ┌───────┴────────┐                           │
│              │  MCP Server    │  27 tools total            │
│              │  (15 Messenger)│  (6 personal + 9 Page)     │
│              └────────────────┘                           │
└─────────────────────────────────────────────────────────┘
```

## Connected accounts

| Account | Type | ID | Method |
|---------|------|----|--------|
| cloudless.gr | Page | `3f2f59c4-f190-44ad-aefe-4321af08ef89` | Messenger Platform API |
| Cloudless.gr | Page | `df912a00-1ecf-4cb6-9ecf-c984584836ea` | Messenger Platform API |
| Themistoklis Baltzakis | Personal | `de7234c0-0209-4243-a08f-ce7b96f1ebc7` | Browser Bridge |

## API base

```
http://127.0.0.1:8083/api/v1/messenger
```

## Authentication

All endpoints (except webhook GET/POST) require a Bearer token from
`POST /api/v1/auth/login`. Helper scripts auto-login from `.env`.

## Page Messenger endpoints (Graph API)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/setup` | Subscribe Page + configure profile |
| GET | `/{account_id}/profile` | Get Messenger Profile |
| PUT | `/{account_id}/profile` | Update profile properties |
| DELETE | `/{account_id}/profile?fields=f1,f2` | Delete profile properties |
| POST | `/{account_id}/unsubscribe` | Remove app subscription |
| POST | `/{account_id}/send` | Send text/image message |
| POST | `/{account_id}/send-quick-replies` | Send quick replies |
| GET | `/{account_id}/conversations` | List conversations |
| GET | `/{account_id}/conversations/{conv_id}` | Get messages in thread |
| GET | `/{account_id}/user/{psid}` | Get user profile by PSID |
| GET | `/{account_id}/auto-reply` | Get AI auto-reply config |
| PUT | `/{account_id}/auto-reply` | Update AI auto-reply config |
| GET | `/webhook` | Webhook verification |
| POST | `/webhook` | Receive webhook events |
| GET | `/sidecar/status` | Sidecar health + stats |

## Personal Messenger endpoints (Browser Bridge)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{account_id}/personal/conversations` | List personal conversations |
| GET | `/{account_id}/personal/conversations/{thread_id}` | Read thread messages |
| POST | `/{account_id}/personal/send` | Send a message |

Personal Messenger requires a logged-in Facebook browser session via noVNC
(`http://localhost:6080/vnc.html`). Start a session with:
```bash
POST /api/v1/profile/browser/start {"platform": "facebook"}
```

## MCP server tools (15 Messenger tools)

The SocialAuto MCP server exposes 27 total tools, 15 for Messenger:

### Page Messenger tools (9)

| Tool | Purpose |
|------|---------|
| `messenger_setup` | Set up Messenger on a Facebook Page |
| `messenger_get_profile` | Get Messenger Profile |
| `messenger_update_profile` | Update profile properties |
| `messenger_send_message` | Send text/image message to PSID |
| `messenger_list_conversations` | List Page conversations |
| `messenger_get_messages` | Get messages in a thread |
| `messenger_get_auto_reply` | Get AI auto-reply config |
| `messenger_set_auto_reply` | Enable/configure AI auto-reply |
| `messenger_unsubscribe` | Remove app subscription |

### Personal + unified tools (6)

| Tool | Purpose |
|------|---------|
| `messenger_list_all_accounts` | List all Messenger-capable accounts (Pages + personal) |
| `messenger_personal_conversations` | List personal Messenger conversations |
| `messenger_personal_messages` | Read personal thread messages |
| `messenger_personal_send` | Send personal Messenger message |
| `messenger_personal_get_auto_reply` | Get personal auto-reply config |
| `messenger_personal_set_auto_reply` | Enable/configure personal auto-reply |

## Webhook sidecar

The `messenger-sidecar` Docker Compose service (port 9230) processes
webhook events asynchronously:

```
Meta → social-api /messenger/webhook (POST)
         ↓ (dispatch via HTTP, 5s timeout)
    messenger-sidecar /process (POST)
         ↓ (async background task)
    ┌────────────────────────────────────┐
    │ 1. Look up Facebook Page account   │
    │ 2. Check auto-reply config         │
    │ 3. Send typing_on indicator         │
    │ 4. Generate AI response (CF/DMR)    │
    │ 5. Send reply via Send API          │
    │ 6. Send typing_off indicator        │
    └────────────────────────────────────┘
```

Features: idempotency (dedup by message_mid), inline fallback if sidecar
is down, AI chain (Cloudflare Workers AI → DMR → fallback text).

## AI auto-reply

Free-first fallback chain:
1. **Cloudflare Workers AI** (free, `@cf/meta/llama-3.1-8b-instruct`)
2. **Docker Model Runner** (local, `ai/qwen3:8b-q4_K_M`)
3. **Fallback text** (static message)

Config stored in `meta_data.messenger_auto_reply`:
```json
{
  "enabled": false,
  "system_prompt": "You are a helpful assistant for {page_name}...",
  "model": "@cf/meta/llama-3.1-8b-instruct",
  "fallback_text": "Thanks for your message!",
  "max_tokens": 200
}
```

### Personal Messenger auto-reply (Celery polling)

Personal Messenger has **no webhook support** (Meta only provides the
Messenger Platform API for Pages). Auto-reply uses a Celery polling task
instead.

**Task**: `app.worker.tasks.personal_messenger.poll_personal_messenger`
**Schedule**: every 120 seconds (Celery beat)
**Queue**: `default`

Flow:
1. Find Facebook personal accounts with auto-reply enabled
2. Fetch conversations via browser bridge
3. For each conversation with a thread_id:
   - Read recent messages
   - Find last inbound message (`sender: "them"`)
   - Check if already replied (seen tracking)
   - Generate AI response (CF Workers AI → DMR → fallback)
   - Send reply via browser bridge
   - Mark as seen, sleep 3s
4. Update `last_checked` timestamp

Config stored in `meta_data.personal_messenger_auto_reply`:
```json
{
  "enabled": true,
  "system_prompt": "You are Themistoklis Baltzakis from Cloudless.gr...",
  "model": "@cf/meta/llama-3.1-8b-instruct",
  "fallback_text": "Thanks for your message! I'll get back to you soon.",
  "max_tokens": 200
}
```

State tracking in `meta_data.personal_messenger_seen`:
```json
{
  "1561707110777254": "last replied message text"
}
```

API endpoints:
- `GET  /api/v1/messenger/{id}/personal/auto-reply` — get config
- `PUT  /api/v1/messenger/{id}/personal/auto-reply` — update config

## Scripts

| Script | Purpose |
|--------|---------|
| `list-accounts.sh` | List all Messenger-capable accounts |
| `page-setup.sh` | Set up Messenger on a Facebook Page |
| `page-send.sh` | Send a Page Messenger message |
| `page-conversations.sh` | List Page conversations |
| `page-auto-reply.sh` | Get/set AI auto-reply config |
| `personal-conversations.sh` | List personal Messenger conversations |
| `personal-send.sh` | Send personal Messenger message |
| `personal-read.sh` | Read personal thread messages |
| `personal-auto-reply.sh` | Get/set personal AI auto-reply config |
| `webhook-test.sh` | Test webhook verification + event |
| `sidecar-status.sh` | Check sidecar health + stats |

## Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `MESSENGER_VERIFY_TOKEN` | Webhook GET verification | `cloudless_messenger_verify` |
| `FACEBOOK_APP_SECRET` | POST signature verification | (from OAuth config) |
| `BROWSER_BRIDGE_URL` | Browser bridge URL | `http://browser-novnc:9223` |
| `MESSENGER_SIDECAR_URL` | Sidecar URL | `http://messenger-sidecar:9230` |
| `CLOUDFLARE_API_TOKEN` | AI auto-reply (Workers AI) | (optional) |
| `DMR_BASE_URL` | Local DMR fallback | `http://localhost:12434` |

## Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Browser bridge error: Navigation failed` | noVNC session not logged in | Open noVNC, log in to Facebook |
| `Personal Messenger requires a Facebook personal (user) account` | Used Page account for personal endpoint | Use the personal account UUID |
| `Messenger setup requires a Facebook Page account` | Used personal account for Page setup | Use a Page account UUID |
| `(#613) Calls to this api have exceeded the rate limit` | >10 profile API calls in 10 min | Wait 10 min, batch updates |
| `No Page access token found` | Page not connected or token missing | Reconnect Facebook account via OAuth |

## GitHub research references

Evaluated open-source Messenger management tools and MCP servers:

### Adopted patterns

| Repo | Language | Pattern adopted |
|------|----------|-----------------|
| [tigerx500darkcore/Messenger-engagement-bot](https://github.com/tigerx500darkcore/Messenger-engagement-bot) | Python | Sidecar architecture for async webhook processing |
| [Prantho-das/ai-chat-bot](https://github.com/Prantho-das/ai-chat-bot) | Python | AI fallback chain concept (CF Workers AI → local) |
| [kstevica/captain-claw](https://github.com/kstevica/captain-claw) | Python | HMAC-SHA256 webhook signature verification |
| [htlin222/fbpost](https://github.com/htlin222/fbpost) | Python | Playwright browser automation for personal Messenger (send, read, search, contacts, E2EE PIN handling, daemon mode, cookie profiles) |
| [Abdulrahman-Daney/Facebook_Messenger_MCP](https://github.com/Abdulrahman-Daney/Facebook_Messenger_MCP) | Python | MCP tool structure for send_text/image/video/file/audio + get_message_details |

### Evaluated — not integrated

| Repo | Language | Reason |
|------|----------|--------|
| [ishan-parihar/facebook-lyr](https://github.com/ishan-parihar/facebook-lyr) | Python | 41-tool cookie-based MCP server with multi-account support. Messenger send is decommissioned (Mercury endpoints return 404). Read side works. Considered for personal Messenger reads but our browser bridge already covers this. |
| [danieljohnbyns/fb-chat-mcp](https://github.com/danieljohnbyns/fb-chat-mcp) | Node.js | 30+ tools including E2EE messaging, media, threads. Uses `meta-messenger.js` FFI with MQTT. AGPL-3.0 license. Requires Node.js 22.12+. Considered for E2EE support but adds Node.js dependency. |
| [leon100/MetaMCP](https://github.com/leon100/MetaMCP) | Python | Unified MCP for Facebook + Instagram + WhatsApp. 4 standardized tools. Demo mode with mock adapters. Too abstract for our needs. |
| [samuelmukoti/facebook-mcp-server](https://github.com/samuelmukoti/facebook-mcp-server) | Python | Facebook Page MCP server for posting, comments, content retrieval. Overlaps with our existing Page management. |
| [IvanBBaev/facebook-mcp](https://github.com/IvanBBaev/facebook-mcp) | TypeScript | TypeScript MCP for Meta Graph API with plan-and-apply write safety. Pre-1.0, only 4 of 35 tools live. |
| [anhln-embedded/fb-mcp-server](https://github.com/anhln-embedded/fb-mcp-server) | Python | Personal Messenger via Chrome Extension + MQTT WebSocket. Bypasses anti-bot. Not policy-compliant. |
| [haonguyenbugs/httpfca](https://github.com/haonguyenbugs/httpfca) | Node.js | Unofficial Facebook Chat API emulating browser. AppState support. Risk of account ban. |
| [Nexus-016/Nexus-fCA](https://github.com/Nexus-016/Nexus-fCA) | JavaScript | Advanced multi-account Messenger API with parallel sends, MQTT, session resilience. Unofficial, ban risk. |
| [0x3EF8/Nero-Facebook-Bot](https://github.com/0x3EF8/Nero-Facebook-Bot) | JavaScript | Modular multi-account Messenger bot with REST API, cookie extractor. Bot framework, not a management tool. |
| [zernio-dev/unified-inbox](https://github.com/zernio-dev/unified-inbox) | TypeScript | Open source unified inbox for WhatsApp, Instagram, Messenger, Telegram, X, Reddit, Bluesky. Requires Zernio API key (SaaS dependency). |
| [Teamingzooper/nexus-client](https://github.com/Teamingzooper/nexus-client) | TypeScript | Desktop client combining messaging apps in one window. Electron-based, not an API. |
| [ras218979/ts-messenger-api](https://github.com/ras218979/ts-messenger-api) | TypeScript | Unofficial Messenger API for user accounts. Unmaintained. |

### Key findings from research

1. **Meta does NOT provide an API for personal Messenger** — only Pages can
   use the Messenger Platform API. Personal Messenger requires browser
   automation or unofficial cookie-based scraping.

2. **Mercury endpoints are decommissioned** — Facebook has removed the
   legacy `send_messages.php` endpoint. Cookie-based sending no longer
   works for many accounts. Browser automation (Playwright) is the
   reliable path for personal Messenger.

3. **E2EE support is complex** — End-to-end encrypted conversations
   require PIN handling and special browser flows. The `htlin222/fbpost`
   project handles this well with Playwright + auto-PIN entry.

4. **Multi-account is a first-class concern** — `facebook-lyr` and
   `Nexus-fCA` both support multiple identities with separate cookie
   stores. Our SocialAuto already handles this via `SocialAccount` model
   with `account_type` field (`page` vs `user`).

5. **Cookie-based reads still work** — While sending via Mercury is dead,
   reading conversations via cookie scraping still works. Our browser
   bridge uses Playwright navigation which is more reliable.

## Future enhancements

- **E2EE support**: Adopt `htlin222/fbpost` patterns for E2EE PIN handling
  in the browser bridge
- **Cookie-based fast reads**: Add `facebook-lyr` style cookie scraping for
  faster conversation reads (no full browser navigation needed)
- **Daemon mode**: Keep a persistent browser session for instant sends
  (like `fbpost daemon` — ~6s per send vs ~15s cold start)
- **Contact caching**: Cache Messenger contacts for name-based lookups
  instead of thread IDs
- **Unified inbox**: Consider `zernio-dev/unified-inbox` patterns for a
  cross-platform DM inbox UI (Messenger + Instagram + WhatsApp + Threads)
