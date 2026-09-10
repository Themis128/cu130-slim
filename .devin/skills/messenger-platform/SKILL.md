---
name: messenger-platform
description: >-
  Manage Facebook Messenger for Pages through SocialAuto: setup, profile
  configuration, sending messages, conversations, webhook handling, AI
  auto-reply, and 24-hour policy compliance. Covers the /api/v1/messenger/*
  endpoints. Use when enabling Messenger on a Facebook Page, configuring
  greeting/menu/ice-breakers, sending replies, viewing conversations,
  debugging webhook verification, signature errors, rate limits, or
  setting up AI auto-reply with Cloudflare Workers AI. For personal
  Messenger management, see the messenger-management skill instead.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Messenger Platform

Manage Facebook Messenger for Pages through the SocialAuto backend API.

> **For personal Messenger accounts**, see the `messenger-management` skill
> which covers both Page Messenger (Graph API) and personal Messenger
> (browser bridge).

## When to use

- Enable Messenger on a connected Facebook Page (setup + subscribe)
- Configure Messenger Profile (greeting, Get Started, persistent menu, ice breakers, whitelisted domains)
- Send text/image messages or quick replies to a person on Messenger
- List conversations and read messages in a thread
- Set up or debug the webhook endpoint (verification + signature)
- Configure AI auto-reply (Cloudflare Workers AI first, DMR fallback)
- Debug Messenger API errors (rate limits, 24-hour window, permissions)
- Unsubscribe a Page from Messenger

## Key concepts

### Messenger is a Facebook Page channel, not a standalone account

Meta does not provide an API to create personal Messenger accounts.
Messenger is enabled on a **Facebook Page** that has:
- A Page access token with `pages_messaging` permission
- The app subscribed to the Page's messaging webhooks
- A configured Messenger Profile (greeting, Get Started, persistent menu)

The SocialAuto `SocialAccount` model stores the Page with:
- `platform = "facebook"`
- `account_type = "page"`
- `meta_data.page_token` = encrypted Page access token
- `meta_data.messenger_setup` = setup state
- `meta_data.messenger_auto_reply` = AI auto-reply config

### 24-hour messaging window

- After a person sends a message to the Page, you have **24 hours** to reply
- Use `messaging_type = "RESPONSE"` for replies within the window
- Outside the window, use message tags (`HUMAN_AGENT`, `ACCOUNT_UPDATE`, etc.)
- Message tags **cannot** be used for promotional content
- Effective April 27, 2026: `CONFIRMED_EVENT_UPDATE`, `ACCOUNT_UPDATE`, and
  `POST_PURCHASE_UPDATE` tags will return error code 100

### Rate limits

- Messenger Profile API: **10 calls per Page per 10 minutes**
- Batch profile updates and avoid unnecessary repeated calls
- The setup endpoint returns `profile.result = "rate_limited"` instead of
  failing when the profile API rate limit is hit (subscription still succeeds)

### Webhook security

- **GET verification**: Meta sends `hub.mode=subscribe`, `hub.verify_token`,
  `hub.challenge` — echo back `hub.challenge` if the token matches
  `MESSENGER_VERIFY_TOKEN`
- **POST signature**: Meta signs every POST body with HMAC-SHA256 keyed by
  the app secret, sent in `X-Hub-Signature-256` as `sha256=<hex>`
- The POST endpoint verifies the signature if `FACEBOOK_APP_SECRET` is set
- Signature verification uses the **raw body bytes** (not re-serialized JSON)
  because Meta signs an escaped-unicode form of the payload

### Greeting field deprecation

Meta silently deprecated the `greeting` field in the Messenger Profile API.
The POST still returns `{"result": "success"}` but the GET no longer returns
the greeting text on Graph API v25.0+. The code still sends it for backward
compatibility, but do not rely on it being persisted. Use `ice_breakers`
instead for welcome messaging.

### Conversation Routing (replaces Handover Protocol)

Meta migrated from Handover Protocol to Conversation Routing:
- All connected apps receive messaging webhooks
- All apps can respond to the same user message
- `pass_thread_control` is enabled for any app
- `request_thread_control` is enabled but must be invoked to gain control
- `take_thread_control` is blocked unless a default app is set
- Thread control expires after 24 hours of inactivity

## API base

```
http://127.0.0.1:8083/api/v1/messenger
```

## Authentication

All endpoints except `/webhook` (GET + POST) require a Bearer token from
`POST /api/v1/auth/login` (form-encoded `username` + `password`). The
helper scripts handle login automatically by reading `.env` for
`SOCIAL_ADMIN_EMAIL` / `SOCIAL_ADMIN_PASSWORD`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/setup` | Subscribe Page + configure default profile |
| GET | `/{account_id}/profile` | Get Messenger Profile (greeting, menu, etc.) |
| PUT | `/{account_id}/profile` | Update profile properties |
| DELETE | `/{account_id}/profile?fields=f1,f2` | Delete profile properties |
| POST | `/{account_id}/unsubscribe` | Remove app subscription from Page |
| POST | `/{account_id}/send` | Send text/image message |
| POST | `/{account_id}/send-quick-replies` | Send message with quick reply buttons |
| GET | `/{account_id}/conversations` | List conversations |
| GET | `/{account_id}/conversations/{conv_id}` | Get messages in a thread |
| GET | `/{account_id}/user/{psid}` | Get user profile by Page-Scoped ID |
| GET | `/{account_id}/auto-reply` | Get AI auto-reply config |
| PUT | `/{account_id}/auto-reply` | Update AI auto-reply config |
| GET | `/webhook` | Webhook verification (Meta → us) |
| POST | `/webhook` | Receive webhook events (Meta → us) |

## Connected Facebook Page

- SocialAuto account ID: `3f2f59c4-f190-44ad-aefe-4321af08ef89`
- Facebook Graph Page ID: `116436681562585`
- Page name: `cloudless.gr`
- Page access token: stored encrypted in `meta_data.page_token`

## Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `MESSENGER_VERIFY_TOKEN` | Webhook GET verification token | `cloudless_messenger_verify` |
| `FACEBOOK_APP_SECRET` | App secret for POST signature verification | (from Facebook OAuth config) |
| `FACEBOOK_CLIENT_ID` | Meta app ID | (from .env) |
| `FACEBOOK_CLIENT_SECRET` | Meta app secret | (from .env) |
| `CLOUDFLARE_API_TOKEN` | For AI auto-reply (Workers AI) | (optional) |
| `CLOUDFLARE_ACCOUNT_ID` | For AI auto-reply (Workers AI) | (optional) |
| `DMR_BASE_URL` | Local Docker Model Runner fallback | `http://localhost:12434` |

## AI auto-reply

The auto-reply system uses a free-first fallback chain:
1. **Cloudflare Workers AI** (free tier, `@cf/meta/llama-3.1-8b-instruct`)
2. **Docker Model Runner** (local, `ai/qwen3:8b-q4_K_M`)
3. **Fallback text** (static message)

Auto-reply is **opt-in** and respects the 24-hour policy window. It only
triggers on incoming `text` or `postback` webhook events. The system sends
a `typing_on` indicator while generating, then sends the reply and turns
off typing.

Configuration is stored in `meta_data.messenger_auto_reply`:
```json
{
  "enabled": false,
  "system_prompt": "You are a helpful assistant for {page_name}...",
  "model": "@cf/meta/llama-3.1-8b-instruct",
  "fallback_text": "Thanks for your message! We'll get back to you soon.",
  "max_tokens": 200
}
```

## Webhook setup (Meta Developer Console)

1. Go to https://developers.facebook.com/apps → your app → Messenger → Settings
2. Set **Callback URL** to: `https://<your-domain>/api/v1/messenger/webhook`
3. Set **Verify Token** to match `MESSENGER_VERIFY_TOKEN` in `.env`
4. Subscribe to fields: `messages`, `messaging_postbacks`, `message_echoes`,
   `messaging_optins`, `messaging_account_linking`
5. For local development, use `social-cloudflared` tunnel or ngrok for HTTPS

## Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `(#613) Calls to this api have exceeded the rate limit` | >10 profile API calls in 10 min | Wait 10 min, batch updates |
| `(#100) Requires one of the params: get_started,...` | PUT profile with only `greeting` | Include at least one required field |
| `Failed to send message` with fake PSID | PSID not valid for this Page | Use PSIDs from webhook events only |
| `Verification failed` (403 on GET webhook) | Wrong verify token | Check `MESSENGER_VERIFY_TOKEN` matches Meta dashboard |
| `Invalid webhook signature` (403 on POST webhook) | Wrong app secret or body mismatch | Verify `FACEBOOK_APP_SECRET` matches Meta app |
| `No Page access token found` | Page not connected or token missing | Reconnect Facebook account via OAuth |

## Scripts

| Script | Purpose |
|--------|---------|
| `setup.sh` | Set up Messenger on a Facebook Page |
| `get-profile.sh` | Get current Messenger Profile |
| `update-profile.sh` | Update greeting/menu/domains |
| `send-message.sh` | Send a text message |
| `list-conversations.sh` | List conversations |
| `get-messages.sh` | Get messages in a conversation |
| `auto-reply.sh` | Get/set AI auto-reply config |
| `webhook-test.sh` | Test webhook verification + event |
| `status.sh` | Check Messenger setup status |

## Research references

- [Messenger Profile API](https://developers.facebook.com/docs/messenger-platform/reference/messenger-profile-api/)
- [Send API](https://developers.facebook.com/docs/messenger-platform/send-messages/)
- [Webhooks](https://developers.facebook.com/docs/messenger-platform/webhooks/)
- [Conversation Routing](https://developers.facebook.com/docs/messenger-platform/conversation-routing/)
- [Messenger Platform Policy](https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy)
- [Graph API Page Messages](https://developers.facebook.com/docs/graph-api/reference/page/messages/)
- [Message Tags](https://developers.facebook.com/docs/messenger-platform/send-messages/message-tags/)
- [Changelog Archive](https://developers.facebook.com/docs/messenger-platform/changelog/archive/)

## Open-source references

- [@warriorteam/messenger-sdk](https://github.com/warriorteam/messenger-sdk) — TypeScript SDK with Conversations API, webhook types, signature verification
- [tigerx500darkcore/Messenger-engagement-bot](https://github.com/tigerx500darkcore/Messenger-engagement-bot) — FastAPI Messenger backend with webhook listener
- [torgodly/messenger-bot](https://github.com/torgodly/messenger-bot) — Laravel Messenger webhooks with BotMan-style API
- [captain-claw meta_webhook_bridge.py](https://github.com/kstevica/captain-claw) — Python webhook signature verification reference
- [hookdeck/facebook-webhooks](https://hookdeck.com/webhooks/skills/facebook-webhooks) — Webhook verification skill reference
- [Abdulrahman-Daney/Facebook_Messenger_MCP](https://github.com/Abdulrahman-Daney/Facebook_Messenger_MCP) — MCP tools for Messenger (send text/image/video/file/audio, get message details)
- [anhln-embedded/fb-mcp-server](https://github.com/anhln-embedded/fb-mcp-server) — MCP bridge for personal Messenger via Chrome extension
- [codustry/songmam](https://github.com/codustry/songmam) — Hypermodern Python Messenger library based on FastAPI with Pydantic models
- [Prantho-das/ai-chat-bot](https://github.com/Prantho-das/ai-chat-bot) — FastAPI AI chatbot with FB Messenger + WhatsApp webhooks and Gemini AI
- [trieu/leo-bot](https://github.com/trieu/leo-bot) — FastAPI AI chatbot with Messenger + Zalo OA, RAG, Redis rate limiting

## MCP server integration

The SocialAuto MCP server (`app/mcp/server.py`) exposes 9 Messenger tools
to AI agents (Claude, Cursor, ChatGPT). The server runs inside the
`social-api` container and is configured in `.devin/mcp_config.json` as
`socialauto`.

### MCP tools

| Tool | Purpose |
|------|---------|
| `messenger_setup` | Set up Messenger on a Facebook Page |
| `messenger_get_profile` | Get Messenger Profile |
| `messenger_update_profile` | Update profile properties |
| `messenger_send_message` | Send text/image message to a PSID |
| `messenger_list_conversations` | List conversations |
| `messenger_get_messages` | Get messages in a thread |
| `messenger_get_auto_reply` | Get AI auto-reply config |
| `messenger_set_auto_reply` | Enable/configure AI auto-reply |
| `messenger_unsubscribe` | Remove app subscription from Page |

### Usage with Claude/Cursor

The MCP server is auto-configured via `.devin/mcp_config.json`:
```json
{
  "mcpServers": {
    "socialauto": {
      "command": "docker",
      "args": ["compose", "exec", "-T", "-e", "SOCIALAUTO_URL=http://social-api:8000", "-e", "SOCIALAUTO_ADMIN_EMAIL=...", "-e", "SOCIALAUTO_ADMIN_PASSWORD=...", "social-api", "python3", "-m", "app.mcp.server"],
      "cwd": "/home/tbaltzakis/cu130-slim"
    }
  }
}
```

## Webhook sidecar

The `messenger-sidecar` Docker Compose service (port 9229) is an async
event processor that receives webhook events from the main API and
processes them without blocking the webhook response (Meta requires 200
within 5 seconds).

### Architecture

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

### Features

- **Idempotency**: Deduplicates by `message_mid` (10,000 entry cache)
- **Fallback**: If sidecar is unavailable, main API processes inline
- **AI chain**: Cloudflare Workers AI (free) → DMR (local) → fallback text
- **Stats**: `GET /stats` shows events received, processed, auto-replies, errors
- **Health**: `GET /health` for Docker healthcheck

### Docker Compose

```yaml
messenger-sidecar:
  build: ./messenger-sidecar
  ports: ["9229:9229"]
  environment:
    - SOCIAL_API_URL=http://social-api:8000
    - CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}
    - DMR_BASE_URL=http://host.docker.internal:12434
```

### GitHub repos evaluated for integration

| Repo | Language | Integration | Status |
|------|----------|-------------|--------|
| `Abdulrahman-Daney/Facebook_Messenger_MCP` | Python | MCP tools for send/get messages | **Evaluated** — our MCP server already covers these tools with SocialAuto's authenticated endpoints |
| `anhln-embedded/fb-mcp-server` | Python | Personal Messenger via Chrome extension | **Not integrated** — bypasses Facebook anti-bot, not policy-compliant for Page messaging |
| `codustry/songmam` | Python | FastAPI + Pydantic Messenger library | **Evaluated** — our `messenger_api.py` already provides this with async httpx |
| `tigerx500darkcore/Messenger-engagement-bot` | Python | FastAPI webhook listener + auto-reply | **Pattern adopted** — sidecar architecture inspired by this approach |
| `trieu/leo-bot` | Python | FastAPI + RAG + Redis rate limiting | **Evaluated** — Redis rate limiting pattern considered for future |
| `Prantho-das/ai-chat-bot` | Python | FastAPI + Gemini AI auto-reply | **Pattern adopted** — AI fallback chain concept |
| `torgodly/messenger-bot` | PHP | Laravel BotMan-style webhook handlers | **Not integrated** — PHP/Laravel, different stack |
| `@warriorteam/messenger-sdk` | TypeScript | Full SDK with webhook types | **Not integrated** — TypeScript, our backend is Python |
| `captain-claw/meta_webhook_bridge.py` | Python | Webhook signature verification | **Pattern adopted** — HMAC-SHA256 verification implemented |
| `hookdeck/facebook-webhooks` | Docs | Webhook verification skill | **Reference** — used for signature verification best practices |
