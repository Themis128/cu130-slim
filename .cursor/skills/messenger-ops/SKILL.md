---
name: messenger-ops
description: >-
  All Facebook Messenger operations: page + personal conversations, auto-reply bot config, webhooks, E2EE limitations, fast reads, sidecar and platform management/upgrades. Use for Messenger inbox, bot, or webhook work.
---

# Messenger Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Messenger E2EE (End-to-End Encrypted Conversations) | `messenger-ops` |
| Messenger Fast Reads (Mobile Basic HTML) | `messenger-ops` |
| Messenger Management | `messenger-ops` → `messenger-management/` |
| Messenger Platform | `messenger-ops` → `messenger-platform/` |
| Messenger Upgrades (Master Index) | `messenger-ops` |

## Messenger E2EE (End-to-End Encrypted Conversations)

Handle end-to-end encrypted (E2EE) conversations in personal Facebook
Messenger. E2EE threads use a different URL path (`/messages/e2ee/t/` instead
of `/messages/t/`) and may show a PIN entry dialog when opening for the
first time.

### When to use

- Read messages from an E2EE conversation
- Send messages to an E2EE conversation
- Debug PIN entry dialog issues
- Set up the MESSENGER_E2EE_PIN environment variable
- Debug E2EE conversations not loading in the browser bridge

### How E2EE works in Messenger

Facebook Messenger E2EE conversations:
- Use URL path `/messages/e2ee/t/{thread_id}/` (vs `/messages/t/{thread_id}/`)
- May require a 6-digit PIN to decrypt messages (shown on first open)
- The PIN is set by the user in Messenger settings
- Once entered, the browser remembers the PIN for the session

### API methods

All methods are on `BrowserBridgeClient` in
`social-automation/backend/app/services/browser_bridge.py`.

#### _navigate_to_thread(thread_id, is_e2ee=False)

Navigates to a specific Messenger thread. For E2EE threads, also calls
the PIN dialog handler automatically.

```python
bridge = BrowserBridgeClient("http://browser-novnc:9223")
## Navigate to an E2EE thread (PIN dialog handled automatically)
await bridge._navigate_to_thread("1234567890", is_e2ee=True)
```

#### _handle_e2ee_pin_dialog(pin=None)

Detects the E2EE PIN entry dialog and enters the PIN if provided.

```python
## Check for PIN dialog (without entering PIN)
found = await bridge._handle_e2ee_pin_dialog()
## Returns: True if dialog found, False if no dialog

## Check for PIN dialog and enter PIN
found = await bridge._handle_e2ee_pin_dialog(pin="123456")
## Returns: True if dialog found and PIN entered
```

#### Reading E2EE messages

```python
## Read messages from an E2EE conversation
result = await bridge.get_personal_messenger_messages_fast(
    thread_id="1234567890",
    is_e2ee=True,
)
```

#### Sending to E2EE conversations

```python
## Send a message to an E2EE conversation
result = await bridge.send_personal_messenger_message(
    thread_id="1234567890",
    text="Hello!",
    is_e2ee=True,
)
```

### PIN dialog detection

The handler looks for the PIN dialog using multiple selectors:

```javascript
// Dialog container
'div[role="dialog"]'
'div[aria-label*="PIN"]'
'div[aria-label*="pin"]'
'div:has(input[type="password"][placeholder*="PIN"])'

// PIN input
'input[type="password"]'
'input[placeholder*="PIN"]'
'input[placeholder*="pin"]'
'input[autocomplete="off"][maxlength="6"]'
```

### Setting the E2EE PIN

```bash
## Set in .env (or docker-compose.yml environment)
MESSENGER_E2EE_PIN=123456

## Or pass directly to the handler
await bridge._handle_e2ee_pin_dialog(pin="123456")
```

### E2EE conversation detection

E2EE conversations are detected by URL path:

```python
## In get_personal_messenger_conversations_fast():
const match = href.match(/messages\/(?:e2ee\/)?t\/([0-9]+)/);
const isE2EE = href.includes('/e2ee/');
```

The `e2ee: true` flag is set on E2EE conversations in the conversation list.

### Integration with polling task

The `poll-personal-messenger` Celery task automatically handles E2EE:

```python
## In personal_messenger.py
for convo in threadable[:20]:
    is_e2ee = convo.get("e2ee", False)
    msgs_result = await bridge.get_personal_messenger_messages_fast(
        thread_id, is_e2ee=is_e2ee
    )
```

### Common issues

| Issue | Cause | Fix |
|-------|-------|-----|
| E2EE messages not loading | PIN dialog not handled | Set `MESSENGER_E2EE_PIN` env var |
| "PIN dialog detected but no PIN provided" | No PIN in env or parameter | Set `MESSENGER_E2EE_PIN` in `.env` |
| E2EE conversation not detected | URL doesn't contain `/e2ee/` | Check conversation URL in browser |
| PIN dialog keeps appearing | Session expired | Re-login via noVNC and re-warm |

### Source files

- `social-automation/backend/app/services/browser_bridge.py` — E2EE PIN handler and navigation
- `social-automation/backend/app/worker/tasks/personal_messenger.py` — Polling task E2EE integration

### Future enhancements

- Auto-detect PIN from Messenger settings (avoid manual env var)
- PIN caching across sessions (store in encrypted storage)
- E2EE message encryption/decryption at the API level (not just browser)
- Support for E2EE group conversations
- Fallback to non-E2EE if PIN entry fails repeatedly

## Messenger Fast Reads (Mobile Basic HTML)

Read personal Facebook Messenger conversations and messages using the
mobile basic version of Facebook (m.facebook.com), which renders server-side
and loads much faster than the full SPA at facebook.com/messages.

### When to use

- Speed up personal Messenger polling (Celery task runs every 120s)
- Reduce browser bridge latency for conversation reads
- Debug slow personal Messenger reads
- Optimize the poll-personal-messenger task performance
- Fallback when the full SPA fails to load

### Performance comparison

| Method | URL | Load time | Rendering |
|--------|-----|-----------|-----------|
| Full SPA | facebook.com/messages | ~5-7s | Client-side (React hydration) |
| Mobile basic (fast) | m.facebook.com/messages | ~2s | Server-side (basic HTML) |
| Savings | — | **~3-4s per read** | — |

### API methods

Both methods are on `BrowserBridgeClient` in
`social-automation/backend/app/services/browser_bridge.py`.

#### get_personal_messenger_conversations_fast()

Read the conversation list via mobile basic HTML. Falls back to the full
SPA method if mobile basic returns no conversations.

```python
bridge = BrowserBridgeClient("http://browser-novnc:9223")
result = await bridge.get_personal_messenger_conversations_fast()
## Returns: { "conversations": [...], "count": N, "source": "mobile_basic" | "full_spa" }
```

#### get_personal_messenger_messages_fast(thread_id, is_e2ee=False)

Read messages from a specific thread via mobile basic HTML. Falls back to
the full SPA method if mobile basic returns no messages.

```python
result = await bridge.get_personal_messenger_messages_fast(
    thread_id="1234567890",
    is_e2ee=False,
)
## Returns: { "messages": [...], "count": N, "source": "mobile_basic" | "full_spa" }
```

### Fallback chain

```
Mobile basic (m.facebook.com)
    │ fails or returns empty
    ▼
Full SPA (facebook.com/messages)
```

The `source` field in the response indicates which method was used:
- `"mobile_basic"` — fast path succeeded
- `"full_spa"` — fell back to full SPA (implicit, when fast returns the SPA result)

### Integration with polling task

The fast reads are wired into the `poll-personal-messenger` Celery task
(`social-automation/backend/app/worker/tasks/personal_messenger.py`):

```python
## 1. Fetch conversations (fast mobile-basic first, fallback to full SPA)
convos_result = await bridge.get_personal_messenger_conversations_fast()

## 2. Read recent messages (fast mobile-basic first, fallback to SPA)
msgs_result = await bridge.get_personal_messenger_messages_fast(thread_id, is_e2ee=is_e2ee)
```

### How it works

1. Navigate to `https://m.facebook.com/messages` (or `m.facebook.com/messages/t/{thread_id}/`)
2. Wait 2 seconds for server-side rendering (vs 5s for SPA hydration)
3. Extract conversation/message data from the basic HTML DOM
4. If no data found, fall back to the full SPA method

The mobile basic version uses simpler HTML:
- Anchor tags with `/messages/t/` or `/messages/e2ee/t/` hrefs
- Bold text (`<strong>`, `<b>`) for unread indicators
- Server-rendered message containers (no React hydration needed)

### Source files

- `social-automation/backend/app/services/browser_bridge.py` — Fast read methods
- `social-automation/backend/app/worker/tasks/personal_messenger.py` — Polling task integration

### Browser bridge endpoints used

- `POST /session/navigate` — Navigate to m.facebook.com URL
- `POST /session/evaluate` — Extract conversation/message data from DOM

### Future enhancements

- Cache conversation list for 30s to avoid repeated reads
- Incremental message reads (only fetch new messages since last read)
- Background pre-fetch of conversation list (daemon mode)
- Support for m.basic.facebook.com (even lighter than m.facebook.com)

## Messenger Management

Manage ALL Facebook Messenger accounts — both Facebook Pages (via official
Messenger Platform API) and personal accounts (via browser bridge).

### When to use

- List all Messenger-capable accounts (Pages + personal)
- Send/receive messages on any Messenger account
- Set up Messenger on a new Facebook Page
- Configure AI auto-reply for Page Messenger
- Read personal Messenger conversations (browser bridge)
- Send personal Messenger messages (browser bridge)
- Debug Messenger webhook issues
- Manage multiple Facebook Pages' Messenger from one place

### Architecture

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

### Connected accounts

| Account | Type | ID | Method |
|---------|------|----|--------|
| cloudless.gr | Page | `3f2f59c4-f190-44ad-aefe-4321af08ef89` | Messenger Platform API |
| Cloudless.gr | Page | `df912a00-1ecf-4cb6-9ecf-c984584836ea` | Messenger Platform API |
| Themistoklis Baltzakis | Personal | `de7234c0-0209-4243-a08f-ce7b96f1ebc7` | Browser Bridge |

### API base

```
http://127.0.0.1:8083/api/v1/messenger
```

### Authentication

All endpoints (except webhook GET/POST) require a Bearer token from
`POST /api/v1/auth/login`. Helper scripts auto-login from `.env`.

### Page Messenger endpoints (Graph API)

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

### Personal Messenger endpoints (Browser Bridge)

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

### MCP server tools (15 Messenger tools)

The SocialAuto MCP server exposes 27 total tools, 15 for Messenger:

#### Page Messenger tools (9)

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

#### Personal + unified tools (6)

| Tool | Purpose |
|------|---------|
| `messenger_list_all_accounts` | List all Messenger-capable accounts (Pages + personal) |
| `messenger_personal_conversations` | List personal Messenger conversations |
| `messenger_personal_messages` | Read personal thread messages |
| `messenger_personal_send` | Send personal Messenger message |
| `messenger_personal_get_auto_reply` | Get personal auto-reply config |
| `messenger_personal_set_auto_reply` | Enable/configure personal auto-reply |

### Webhook sidecar

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

### AI auto-reply

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

#### Personal Messenger auto-reply (Celery polling)

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

### Scripts

| Script | Purpose |
|--------|---------|
| `list-accounts.py` | List all Messenger-capable accounts |
| `page-setup.py` | Set up Messenger on a Facebook Page |
| `page-send.py` | Send a Page Messenger message |
| `page-conversations.py` | List Page conversations |
| `page-auto-reply.py` | Get/set AI auto-reply config |
| `personal-conversations.py` | List personal Messenger conversations |
| `personal-send.py` | Send personal Messenger message |
| `personal-read.py` | Read personal thread messages |
| `personal-auto-reply.py` | Get/set personal AI auto-reply config |
| `webhook-test.py` | Test webhook verification + event |
| `sidecar-status.py` | Check sidecar health + stats |

### Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `MESSENGER_VERIFY_TOKEN` | Webhook GET verification | `cloudless_messenger_verify` |
| `FACEBOOK_APP_SECRET` | POST signature verification | (from OAuth config) |
| `BROWSER_BRIDGE_URL` | Browser bridge URL | `http://browser-novnc:9223` |
| `MESSENGER_SIDECAR_URL` | Sidecar URL | `http://messenger-sidecar:9230` |
| `CLOUDFLARE_API_TOKEN` | AI auto-reply (Workers AI) | (optional) |
| `DMR_BASE_URL` | Local DMR fallback | `http://localhost:12435` |

### Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Browser bridge error: Navigation failed` | noVNC session not logged in | Open noVNC, log in to Facebook |
| `Personal Messenger requires a Facebook personal (user) account` | Used Page account for personal endpoint | Use the personal account UUID |
| `Messenger setup requires a Facebook Page account` | Used personal account for Page setup | Use a Page account UUID |
| `(#613) Calls to this api have exceeded the rate limit` | >10 profile API calls in 10 min | Wait 10 min, batch updates |
| `No Page access token found` | Page not connected or token missing | Reconnect Facebook account via OAuth |

### GitHub research references

Evaluated open-source Messenger management tools and MCP servers:

#### Adopted patterns

| Repo | Language | Pattern adopted |
|------|----------|-----------------|
| [tigerx500darkcore/Messenger-engagement-bot](https://github.com/tigerx500darkcore/Messenger-engagement-bot) | Python | Sidecar architecture for async webhook processing |
| [Prantho-das/ai-chat-bot](https://github.com/Prantho-das/ai-chat-bot) | Python | AI fallback chain concept (CF Workers AI → local) |
| [kstevica/captain-claw](https://github.com/kstevica/captain-claw) | Python | HMAC-SHA256 webhook signature verification |
| [htlin222/fbpost](https://github.com/htlin222/fbpost) | Python | Playwright browser automation for personal Messenger (send, read, search, contacts, E2EE PIN handling, daemon mode, cookie profiles) |
| [Abdulrahman-Daney/Facebook_Messenger_MCP](https://github.com/Abdulrahman-Daney/Facebook_Messenger_MCP) | Python | MCP tool structure for send_text/image/video/file/audio + get_message_details |

#### Evaluated — not integrated

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

#### Key findings from research

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

### Future enhancements

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

## Messenger Platform

Manage Facebook Messenger for Pages through the SocialAuto backend API.

> **For personal Messenger accounts**, see the `messenger-ops` skill
> which covers both Page Messenger (Graph API) and personal Messenger
> (browser bridge).

### When to use

- Enable Messenger on a connected Facebook Page (setup + subscribe)
- Configure Messenger Profile (greeting, Get Started, persistent menu, ice breakers, whitelisted domains)
- Send text/image messages or quick replies to a person on Messenger
- List conversations and read messages in a thread
- Set up or debug the webhook endpoint (verification + signature)
- Configure AI auto-reply (Cloudflare Workers AI first, DMR fallback)
- Debug Messenger API errors (rate limits, 24-hour window, permissions)
- Unsubscribe a Page from Messenger

### Key concepts

#### Messenger is a Facebook Page channel, not a standalone account

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

#### 24-hour messaging window

- After a person sends a message to the Page, you have **24 hours** to reply
- Use `messaging_type = "RESPONSE"` for replies within the window
- Outside the window, use message tags (`HUMAN_AGENT`, `ACCOUNT_UPDATE`, etc.)
- Message tags **cannot** be used for promotional content
- Effective April 27, 2026: `CONFIRMED_EVENT_UPDATE`, `ACCOUNT_UPDATE`, and
  `POST_PURCHASE_UPDATE` tags will return error code 100

#### Rate limits

- Messenger Profile API: **10 calls per Page per 10 minutes**
- Batch profile updates and avoid unnecessary repeated calls
- The setup endpoint returns `profile.result = "rate_limited"` instead of
  failing when the profile API rate limit is hit (subscription still succeeds)

#### Webhook security

- **GET verification**: Meta sends `hub.mode=subscribe`, `hub.verify_token`,
  `hub.challenge` — echo back `hub.challenge` if the token matches
  `MESSENGER_VERIFY_TOKEN`
- **POST signature**: Meta signs every POST body with HMAC-SHA256 keyed by
  the app secret, sent in `X-Hub-Signature-256` as `sha256=<hex>`
- The POST endpoint verifies the signature if `FACEBOOK_APP_SECRET` is set
- Signature verification uses the **raw body bytes** (not re-serialized JSON)
  because Meta signs an escaped-unicode form of the payload

#### Greeting field deprecation

Meta silently deprecated the `greeting` field in the Messenger Profile API.
The POST still returns `{"result": "success"}` but the GET no longer returns
the greeting text on Graph API v25.0+. The code still sends it for backward
compatibility, but do not rely on it being persisted. Use `ice_breakers`
instead for welcome messaging.

#### Conversation Routing (replaces Handover Protocol)

Meta migrated from Handover Protocol to Conversation Routing:
- All connected apps receive messaging webhooks
- All apps can respond to the same user message
- `pass_thread_control` is enabled for any app
- `request_thread_control` is enabled but must be invoked to gain control
- `take_thread_control` is blocked unless a default app is set
- Thread control expires after 24 hours of inactivity

### API base

```
http://127.0.0.1:8083/api/v1/messenger
```

### Authentication

All endpoints except `/webhook` (GET + POST) require a Bearer token from
`POST /api/v1/auth/login` (form-encoded `username` + `password`). The
helper scripts handle login automatically by reading `.env` for
`SOCIAL_ADMIN_EMAIL` / `SOCIAL_ADMIN_PASSWORD`.

### Endpoints

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

### Connected Facebook Page

- SocialAuto account ID: `3f2f59c4-f190-44ad-aefe-4321af08ef89`
- Facebook Graph Page ID: `116436681562585`
- Page name: `cloudless.gr`
- Page access token: stored encrypted in `meta_data.page_token`

### Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `MESSENGER_VERIFY_TOKEN` | Webhook GET verification token | `cloudless_messenger_verify` |
| `FACEBOOK_APP_SECRET` | App secret for POST signature verification | (from Facebook OAuth config) |
| `FACEBOOK_CLIENT_ID` | Meta app ID | (from .env) |
| `FACEBOOK_CLIENT_SECRET` | Meta app secret | (from .env) |
| `CLOUDFLARE_API_TOKEN` | For AI auto-reply (Workers AI) | (optional) |
| `CLOUDFLARE_ACCOUNT_ID` | For AI auto-reply (Workers AI) | (optional) |
| `DMR_BASE_URL` | Local Docker Model Runner fallback | `http://localhost:12435` |

### AI auto-reply

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

### Webhook setup (Meta Developer Console)

1. Go to https://developers.facebook.com/apps → your app → Messenger → Settings
2. Set **Callback URL** to: `https://<your-domain>/api/v1/messenger/webhook`
3. Set **Verify Token** to match `MESSENGER_VERIFY_TOKEN` in `.env`
4. Subscribe to fields: `messages`, `messaging_postbacks`, `message_echoes`,
   `messaging_optins`, `messaging_account_linking`
5. For local development, use `social-cloudflared` tunnel or ngrok for HTTPS

### Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `(#613) Calls to this api have exceeded the rate limit` | >10 profile API calls in 10 min | Wait 10 min, batch updates |
| `(#100) Requires one of the params: get_started,...` | PUT profile with only `greeting` | Include at least one required field |
| `Failed to send message` with fake PSID | PSID not valid for this Page | Use PSIDs from webhook events only |
| `Verification failed` (403 on GET webhook) | Wrong verify token | Check `MESSENGER_VERIFY_TOKEN` matches Meta dashboard |
| `Invalid webhook signature` (403 on POST webhook) | Wrong app secret or body mismatch | Verify `FACEBOOK_APP_SECRET` matches Meta app |
| `No Page access token found` | Page not connected or token missing | Reconnect Facebook account via OAuth |

### Scripts

| Script | Purpose |
|--------|---------|
| `setup.py` | Set up Messenger on a Facebook Page |
| `get-profile.py` | Get current Messenger Profile |
| `update-profile.py` | Update greeting/menu/domains |
| `send-message.py` | Send a text message |
| `list-conversations.py` | List conversations |
| `get-messages.py` | Get messages in a conversation |
| `auto-reply.py` | Get/set AI auto-reply config |
| `webhook-test.py` | Test webhook verification + event |
| `status.py` | Check Messenger setup status |

### Research references

- [Messenger Profile API](https://developers.facebook.com/docs/messenger-platform/reference/messenger-profile-api/)
- [Send API](https://developers.facebook.com/docs/messenger-platform/send-messages/)
- [Webhooks](https://developers.facebook.com/docs/messenger-platform/webhooks/)
- [Conversation Routing](https://developers.facebook.com/docs/messenger-platform/conversation-routing/)
- [Messenger Platform Policy](https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy)
- [Graph API Page Messages](https://developers.facebook.com/docs/graph-api/reference/page/messages/)
- [Message Tags](https://developers.facebook.com/docs/messenger-platform/send-messages/message-tags/)
- [Changelog Archive](https://developers.facebook.com/docs/messenger-platform/changelog/archive/)

### Open-source references

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

### MCP server integration

The SocialAuto MCP server (`app/mcp/server.py`) exposes 15 Messenger tools
(9 Page + 6 personal/unified) as part of 27 total tools. The server runs
inside the `social-api` container and is configured in `.devin/mcp_config.json`
as `socialauto`.

#### MCP tools — Page Messenger (9)

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

#### MCP tools — Personal Messenger (6)

| Tool | Purpose |
|------|---------|
| `messenger_list_all_accounts` | List all Messenger-capable accounts (Pages + personal) |
| `messenger_personal_conversations` | List personal Messenger conversations (browser bridge) |
| `messenger_personal_messages` | Read personal thread messages (browser bridge) |
| `messenger_personal_send` | Send personal Messenger message (browser bridge) |
| `messenger_personal_get_auto_reply` | Get personal auto-reply config |
| `messenger_personal_set_auto_reply` | Enable/configure personal auto-reply |

See `.devin/skills/messenger-ops/SKILL.md` for personal Messenger
architecture, Celery polling task, and browser bridge details.

#### Usage with Claude/Cursor

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

### Webhook sidecar

The `messenger-sidecar` Docker Compose service (port 9229) is an async
event processor that receives webhook events from the main API and
processes them without blocking the webhook response (Meta requires 200
within 5 seconds).

#### Architecture

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

#### Features

- **Idempotency**: Deduplicates by `message_mid` (10,000 entry cache)
- **Fallback**: If sidecar is unavailable, main API processes inline
- **AI chain**: Cloudflare Workers AI (free) → DMR (local) → fallback text
- **Stats**: `GET /stats` shows events received, processed, auto-replies, errors
- **Health**: `GET /health` for Docker healthcheck

#### Docker Compose

```yaml
messenger-sidecar:
  build: ./messenger-sidecar
  ports: ["9229:9229"]
  environment:
    - SOCIAL_API_URL=http://social-api:8000
    - CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}
    - DMR_BASE_URL=http://host.docker.internal:12435
```

#### GitHub repos evaluated for integration

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

## Messenger Upgrades (Master Index)

Index of all Messenger and browser bridge upgrades implemented in the
Cloudless social stack. Each upgrade has a dedicated skill with full
documentation.

### Upgrade summary

| # | Upgrade | Skill | Status | Commit |
|---|---------|------|--------|--------|
| 1 | Unified inbox API | `unified-inbox` | ✅ Live | `8d359ed3` |
| 2 | Instagram DM integration | `instagram-ops` | ✅ Live | `d1e966b9` |
| 3 | Fast mobile reads | `messenger-ops` | ✅ Live | `ca2385fa` |
| 4 | Browser daemon mode | `browser-ops` | ✅ Live | `717569fa` |
| 5 | E2EE PIN handling | `messenger-ops` | ✅ Live | `169e28ed` |
| 6 | Recruiting bot mode | `bot-guardrails` | ✅ Live | (config only) |
| 7 | LinkedIn sidecar fixes | `browser-ops` | ✅ Live | `37ccde47` |

### When to use which skill

| Task | Skill |
|------|-------|
| View all DMs across platforms | `unified-inbox` |
| Send/read Instagram DMs | `instagram-ops` |
| Speed up personal Messenger reads | `messenger-ops` |
| Keep browser session warm for fast sends | `browser-ops` |
| Handle E2EE PIN dialog | `messenger-ops` |
| Configure recruiting auto-reply | `bot-guardrails` |
| Update LinkedIn profile via SocialAuto | `browser-ops` |

### Architecture (with upgrades)

```
                    Meta Developer Console
                            │
                    Webhook (POST events)
                            │
                            ▼
    social-api (port 8083)
    /api/v1/messenger/webhook    /api/v1/inbox/inbox (NEW)
         │                    │
         │ (dispatch)         │ (aggregate)
         ▼                    ▼
    messenger-sidecar     ┌── Page Messenger (Graph API)
    (port 9230)           ├── Personal Messenger (fast mobile read) (NEW)
         │                ├── Instagram DMs (Messaging API) (NEW)
         │ (AI reply)     └── WhatsApp (Cloud API, placeholder)
         ▼
    CF Workers AI
    → DMR fallback
    → static text

    Browser Bridge (port 9223)
    ├── Daemon mode: warm + keepalive (NEW)
    ├── Fast reads: m.facebook.com (NEW)
    ├── E2EE PIN handling (NEW)
    └── Recruiting bot mode (NEW)

    LinkedIn Sidecar (port 9225)
    ├── Fixed: specialties, headline, About, profile scroll
    ├── Session injection from SocialAuto
    └── Lazy-load handling
```

### Test gate results

All upgrades passed the test gate:

| Check | Result |
|-------|--------|
| `pytest tests/unit -q` | ✅ 549 passed, 2 skipped |
| `ruff check` (all changed files) | ✅ All checks passed |
| `docker compose config --quiet` | ✅ Valid |
| `curl http://localhost:8083/health` | ✅ ok |
| Instagram API tests | ✅ 34 passed |

### Related skills

- `messenger-ops` — Core Messenger management (Pages + personal)
- `messenger-ops` — Facebook Page Messenger via Graph API
- `browser-ops` — Generic browser bridge operations
- `social-stack-ops` — Docker Compose stack operations
