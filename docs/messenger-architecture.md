# SocialAuto Messenger Architecture (Complete)

## Overview

Full Messenger management for all Facebook account types — Facebook Pages
(official Messenger Platform API) and personal accounts (browser bridge).
The system supports sending/receiving messages, AI auto-reply, webhook
processing, and unified MCP tool access across both channels.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SocialAuto Messenger System                       │
│                                                                          │
│   ┌──────────────┐         ┌──────────────┐         ┌──────────────┐     │
│   │  Frontend    │         │  SocialAuto  │         │  MCP Server  │     │
│   │  (Next.js)   │         │  REST API    │         │  (27 tools)  │     │
│   │              │         │  (FastAPI)   │         │              │     │
│   │ /messenger   │────────▶│              │◀────────│  13 Messenger│     │
│   │              │         │              │         │  tools       │     │
│   │ Page inbox   │         │  /api/v1/    │         │              │     │
│   │ Personal     │         │  messenger/* │         │  Page (9)    │     │
│   │ inbox        │         │              │         │  Personal (4)│     │
│   │ Auto-reply   │         │              │         │  Unified (2) │     │
│   │ settings     │         │              │         │              │     │
│   └──────────────┘         └──────┬───────┘         └──────────────┘     │
│                                    │                                      │
│             ┌──────────────────────┼──────────────────────┐              │
│             ▼                      ▼                      ▼              │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
│   │  Page Messenger  │  │  Personal        │  │  Webhook         │      │
│   │  (Graph API)     │  │  Messenger       │  │  Sidecar         │      │
│   │                  │  │  (Browser Bridge)│  │  (port 9230)     │      │
│   │  • Send API      │  │                  │  │                  │      │
│   │  • Profile API   │  │  • Playwright    │  │  • Event dedup   │      │
│   │  • Conversations │  │  • noVNC login   │  │  • AI auto-reply │      │
│   │  • Webhooks      │  │  • Cookie session│  │  • Async process │      │
│   │  • AI auto-reply │  │  • DOM scraping  │  │  • Stats/health  │      │
│   │                  │  │                  │  │                  │      │
│   │  Token: Page     │  │  Auth: Browser   │  │  Token: Page     │      │
│   │  Scopes:         │  │  session via     │  │  (from social-   │      │
│   │  pages_messaging │  │  noVNC (6080)    │  │   api admin)     │      │
│   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘      │
│            │                      │                      │                │
│            ▼                      ▼                      ▼                │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
│   │  Meta Graph API  │  │  Browser Bridge  │  │  Celery Beat     │      │
│   │  v25.0           │  │  (port 9223)     │  │  (every 2 min)   │      │
│   │                  │  │                  │  │                  │      │
│   │  • /{page}/      │  │  • Chromium       │  │  poll_personal_  │      │
│   │    messages      │  │  • facebook.com/  │  │  messenger()     │      │
│   │  • /{page}/      │  │    messages      │  │                  │      │
│   │    message_      │  │  • E2EE threads  │  │  Polls personal  │      │
│   │    subscriptions │  │  • PIN handling   │  │  conversations   │      │
│   │  • /me/          │  │                  │  │  → AI reply      │      │
│   │    conversations │  │  (no API key)    │  │  → browser send  │      │
│   └──────────────────┘  └──────────────────┘  └──────────────────┘      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Two Messenger Channels

### Channel 1: Page Messenger (Official API)

```
┌─────────────┐    Webhook     ┌─────────────┐    Dispatch    ┌─────────────┐
│  Meta / FB   │───POST───────▶│  social-api  │───POST───────▶│  Sidecar    │
│  Platform    │   (5s limit)  │  /webhook    │   /process    │  (port 9230)│
│              │               │              │               │             │
│  Graph API   │◀──Send API───│              │◀──AI reply───│  1. Dedup    │
│  v25.0       │               │              │               │  2. Config  │
│              │               │              │               │  3. Typing  │
│  • PSID      │               │              │               │  4. AI gen  │
│  • 24h window│               │              │               │  5. Send    │
│  • Message   │               │              │               │  6. Typing  │
│    tags      │               │              │               │     off     │
└─────────────┘               └─────────────┘               └─────────────┘
       │
       │ AI Fallback Chain:
       ▼
  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │  Cloudflare      │─▶│  Docker Model    │─▶│  Fallback text   │
  │  Workers AI      │  │  Runner (DMR)    │  │  (static)        │
  │  (free tier)     │  │  (local GPU)     │  │                  │
  │                  │  │                  │  │                  │
  │  llama-3.1-8b    │  │  qwen3:8b        │  │                  │
  │  instruct        │  │  q4_K_M          │  │                  │
  └──────────────────┘  └──────────────────┘  └──────────────────┘
```

**Connected Pages:**

| Page | Account ID | Page ID | Setup |
|------|-----------|---------|-------|
| Cloudless.gr | `df912a00-...` | `1163886186808102` | subscribed, get_started, persistent_menu |
| cloudless.gr | `3f2f59c4-...` | `116436681562585` | subscribed, get_started, persistent_menu |

### Channel 2: Personal Messenger (Browser Bridge)

```
┌─────────────┐  noVNC (6080)  ┌─────────────┐  CDP (9223)  ┌─────────────┐
│  User       │───login──────▶│  browser-   │──control───▶│  Chromium   │
│  (admin)    │               │  novnc       │             │  (headless) │
│             │               │  container   │             │             │
│  Opens      │               │              │             │  facebook.  │
│  vnc.html   │               │  VNC → CDP   │             │  com/       │
│  in browser │               │  bridge      │             │  messages   │
└─────────────┘               └─────────────┘             └─────────────┘
                                                                │
                                                                │ DOM
                                                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Browser Bridge Client                            │
│                     (app/services/browser_bridge.py)                 │
│                                                                      │
│  get_personal_messenger_conversations()                              │
│    → Navigate to facebook.com/messages/                             │
│    → Extract [role="navigation"] a[href*="/messages/"]               │
│    → Return [{name, preview, thread_id, url, unread}]               │
│                                                                      │
│  get_personal_messenger_messages(thread_id)                          │
│    → Navigate to facebook.com/messages/t/{thread_id}/                │
│    → Extract [role="main"] [data-scope="messages_table"] > div       │
│    → Return [{text, sender: "me"|"them", timestamp}]                 │
│                                                                      │
│  send_personal_messenger_message(thread_id, text)                    │
│    → Navigate to thread                                              │
│    → Fill [contenteditable="true"][role="textbox"]                  │
│    → Dispatch KeyboardEvent("Enter")                                 │
│    → Fallback: click send button                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Connected Personal Account:**

| Account | ID | Facebook User ID | Browser Session |
|---------|----|-------------------|-----------------|
| Themistoklis Baltzakis | `de7234c0-...` | `10239451610085137` | Logged in via noVNC |

## Personal Messenger Auto-Reply (Polling)

Personal Messenger has **no webhook support** (Meta only provides the
Messenger Platform API for Pages). Auto-reply uses a Celery polling task.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Celery Beat Schedule                              │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  poll-personal-messenger (every 120s)                       │   │
│  │  Queue: default                                             │   │
│  │  Task: app.worker.tasks.personal_messenger                   │   │
│  │        .poll_personal_messenger                             │   │
│  └──────────────────────┬──────────────────────────────────────┘   │
│                         ▼                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  1. Find Facebook personal accounts with auto-reply enabled │   │
│  │  2. For each account:                                       │   │
│  │     a. Fetch conversations via browser bridge               │   │
│  │     b. For each conversation with thread_id:               │   │
│  │        - Read recent messages                               │   │
│  │        - Find last inbound message ("them")                  │   │
│  │        - Check if already replied (seen tracking)            │   │
│  │        - Generate AI response (CF Workers AI → DMR → text)   │   │
│  │        - Send reply via browser bridge                       │   │
│  │        - Mark as seen, sleep 3s                             │   │
│  │  3. Update last_checked timestamp                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  State tracking (SocialAccount.meta_data):                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  personal_messenger_auto_reply:                             │   │
│  │    enabled: true                                            │   │
│  │    system_prompt: "You are {name}..."                       │   │
│  │    model: "@cf/meta/llama-3.1-8b-instruct"                  │   │
│  │    fallback_text: "Thanks for your message!"                │   │
│  │    max_tokens: 200                                          │   │
│  │                                                             │   │
│  │  personal_messenger_seen:                                  │   │
│  │    {thread_id: last_replied_message_text}                   │   │
│  │                                                             │   │
│  │  personal_messenger_last_checked: "2026-09-11T01:53:..."     │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## MCP Server (27 Tools)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SocialAuto MCP Server                            │
│                    (app/mcp/server.py)                              │
│                    stdio transport, 27 tools                        │
│                                                                     │
│  ┌─────────────────────── Core (12) ──────────────────────────┐    │
│  │  list_accounts, get_account, create_post, list_posts,      │    │
│  │  publish_post, generate_content, suggest_hashtags,         │    │
│  │  score_content, get_analytics, list_media, get_profile,    │    │
│  │  get_brand                                                 │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  ┌─────────────── Page Messenger (9) ────────────────────────┐     │
│  │  messenger_setup              messenger_send_message      │     │
│  │  messenger_get_profile        messenger_list_conversations│     │
│  │  messenger_update_profile     messenger_get_messages       │     │
│  │  messenger_get_auto_reply     messenger_set_auto_reply     │     │
│  │  messenger_unsubscribe                                    │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                     │
│  ┌─────────────── Personal Messenger (4) ───────────────────┐      │
│  │  messenger_personal_conversations                        │      │
│  │  messenger_personal_messages                             │      │
│  │  messenger_personal_send                                 │      │
│  │  messenger_list_all_accounts (unified)                   │      │
│  └──────────────────────────────────────────────────────────┘      │
│                                                                     │
│  ┌─────────────── Personal Auto-Reply (2) ──────────────────┐      │
│  │  messenger_personal_get_auto_reply                        │      │
│  │  messenger_personal_set_auto_reply                        │      │
│  └──────────────────────────────────────────────────────────┘      │
│                                                                     │
│  Transport: docker compose exec social-api python3 -m app.mcp.server│
│  Config: .devin/mcp_config.json → "socialauto"                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Frontend Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Frontend (/messenger)                             │
│                    Next.js App Router                                 │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  messenger/page.tsx                                         │   │
│  │                                                             │   │
│  │  ┌─────────────────────────────────────────────────────┐   │   │
│  │  │  Sidecar Status Card                                 │   │   │
│  │  │  (online/offline, events, auto-replies, errors)      │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  │                                                             │   │
│  │  ┌─────────────────────────────────────────────────────┐   │   │
│  │  │  Account Selector                                    │   │   │
│  │  │  ┌─────────────────────┐  ┌─────────────────────┐    │   │   │
│  │  │  │  Pages (API)        │  │  Personal (Bridge)  │    │   │   │
│  │  │  │  ● Cloudless.gr     │  │  👤 Themistoklis    │    │   │   │
│  │  │  │  ● cloudless.gr     │  │     Baltzakis       │    │   │   │
│  │  │  └─────────────────────┘  └─────────────────────┘    │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  │                                                             │   │
│  │  ┌─────────────────────────────────────────────────────┐   │   │
│  │  │  MessengerInbox (account-type-aware)                 │   │   │
│  │  │                                                       │   │   │
│  │  │  if accountType === "page":                          │   │   │
│  │  │    • Graph API conversations                         │   │   │
│  │  │    • PSID-based send                                 │   │   │
│  │  │    • Setup button                                    │   │   │
│  │  │    • Page AutoReplySettings                          │   │   │
│  │  │                                                       │   │   │
│  │  │  if accountType === "user":                          │   │   │
│  │  │    • Browser bridge conversations                    │   │   │
│  │  │    • Thread-based send                               │   │   │
│  │  │    • noVNC link                                      │   │   │
│  │  │    • Browser bridge notice                           │   │   │
│  │  │    • PersonalAutoReplySettings                       │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  API Service (src/services/api.ts):                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  messengerApi:                                            │   │
│  │    Page: setup, getProfile, updateProfile, sendMessage,    │   │
│  │          getConversations, getConversationMessages,        │   │
│  │          getAutoReplyConfig, updateAutoReplyConfig,        │   │
│  │          getSidecarStatus, unsubscribe                     │   │
│  │    Personal: getPersonalConversations, getPersonalMessages,│   │
│  │              sendPersonalMessage,                          │   │
│  │              getPersonalAutoReply,                        │   │
│  │              updatePersonalAutoReply                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Docker Compose Services

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Docker Compose Stack                              │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ social-api  │  │ social-     │  │ social-     │  │ social-    │ │
│  │ :8083      │  │ frontend    │  │ worker-     │  │ worker-    │ │
│  │             │  │ :8082       │  │ publishing │  │ media      │ │
│  │ FastAPI     │  │ Next.js     │  │             │  │            │ │
│  │ REST API    │  │             │  │ Celery      │  │ Celery     │ │
│  │ MCP server  │  │             │  │ publishing  │  │ media      │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘ │
│         │                │                │                │        │
│  ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐  ┌─────┴──────┐ │
│  │ social-     │  │ browser-    │  │ messenger-  │  │ celery-beat│ │
│  │ worker-     │  │ novnc       │  │ sidecar     │  │            │ │
│  │ default     │  │ :6080 (VNC) │  │ :9230       │  │ Scheduler  │ │
│  │             │  │ :9223 (CDP) │  │             │  │            │ │
│  │ Celery      │  │             │  │ FastAPI     │  │ beat_      │ │
│  │ default     │  │ Chromium    │  │ sidecar     │  │ schedule   │ │
│  │             │  │             │  │             │  │            │ │
│  │ poll_       │  │ facebook.com│  │ AI auto-    │  │ 120s:      │ │
│  │ personal_   │  │ sessions    │  │ reply       │  │ poll       │ │
│  │ messenger   │  │             │  │             │  │ personal   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ redis       │  │ social-     │  │ warp-proxy  │  │ DMR (host) │ │
│  │ :6379       │  │ postgres    │  │ :1080       │  │ :12434     │ │
│  │             │  │             │  │             │  │            │ │
│  │ Celery      │  │ social_     │  │ Cloudflare  │  │ Docker    │ │
│  │ broker      │  │ automation  │  │ WARP SOCKS5 │  │ Model     │ │
│  │             │  │ DB          │  │ (free proxy)│  │ Runner    │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Flow: Page Message (Webhook → Auto-Reply)

```
User sends message     Meta webhook         social-api           sidecar
to Facebook Page        (POST, 5s)          /webhook             /process
     │                    │                    │                    │
     │  1. User types     │                    │                    │
     │───────────────────▶│                    │                    │
     │                    │  2. POST event      │                    │
     │                    │───────────────────▶│                    │
     │                    │                    │  3. Verify HMAC    │
     │                    │                    │  4. Dispatch       │
     │                    │                    │───────────────────▶│
     │                    │  5. Return 200     │                    │
     │                    │◀───────────────────│                    │
     │                    │                    │                    │
     │                    │                    │              6. Dedup (mid)
     │                    │                    │              7. Config check
     │                    │                    │              8. typing_on
     │                    │                    │              9. AI generate
     │                    │                    │                 CF Workers AI
     │                    │                    │                 → DMR
     │                    │                    │                 → fallback
     │                    │                    │             10. Send reply
     │                    │                    │             11. typing_off
     │                    │                    │                    │
     │  12. Reply appears │                    │                    │
     │◀───────────────────│◀───────────────────│◀───────────────────│
```

## Data Flow: Personal Message (Polling → Auto-Reply)

```
Contact sends message   Browser Bridge       Celery Task          AI
to personal Messenger   (Chromium)           (every 2 min)
     │                    │                    │                    │
     │  1. Message        │                    │                    │
     │───────────────────▶│                    │                    │
     │                    │  (DOM updated)     │                    │
     │                    │                    │                    │
     │                    │                    │  2. Poll trigger   │
     │                    │◀───────────────────│                    │
     │                    │  3. Get convos     │                    │
     │                    │───────────────────▶│                    │
     │                    │  [{name, thread_id}]│                    │
     │                    │                    │                    │
     │                    │                    │  4. Read thread    │
     │                    │◀───────────────────│                    │
     │                    │  5. Get messages   │                    │
     │                    │───────────────────▶│                    │
     │                    │  [{text, sender}]  │                    │
     │                    │                    │                    │
     │                    │                    │  6. Find new "them"│
     │                    │                    │  7. Check seen     │
     │                    │                    │  8. AI generate   │
     │                    │                    │───────────────────▶│
     │                    │                    │  9. Reply text     │
     │                    │                    │◀───────────────────│
     │                    │                    │ 10. Send via bridge│
     │                    │◀───────────────────│                    │
     │                    │  11. Type + Enter  │                    │
     │  12. Reply appears │                    │                    │
     │◀───────────────────│                    │                    │
```

## API Endpoints

### Page Messenger (Graph API)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/{id}/setup` | Bearer | Subscribe Page + configure profile |
| GET | `/{id}/profile` | Bearer | Get Messenger Profile |
| PUT | `/{id}/profile` | Bearer | Update profile properties |
| DELETE | `/{id}/profile` | Bearer | Delete profile properties |
| POST | `/{id}/unsubscribe` | Bearer | Remove app subscription |
| POST | `/{id}/send` | Bearer | Send text/image message |
| POST | `/{id}/send-quick-replies` | Bearer | Send quick replies |
| GET | `/{id}/conversations` | Bearer | List conversations |
| GET | `/{id}/conversations/{cid}` | Bearer | Get thread messages |
| GET | `/{id}/user/{psid}` | Bearer | Get user profile |
| GET | `/{id}/auto-reply` | Bearer | Get AI auto-reply config |
| PUT | `/{id}/auto-reply` | Bearer | Update AI auto-reply config |
| GET | `/webhook` | Token | Webhook verification (Meta → us) |
| POST | `/webhook` | HMAC | Receive webhook events (Meta → us) |
| GET | `/sidecar/status` | Bearer | Sidecar health + stats |

### Personal Messenger (Browser Bridge)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/{id}/personal/conversations` | Bearer | List conversations |
| GET | `/{id}/personal/conversations/{tid}` | Bearer | Read thread messages |
| POST | `/{id}/personal/send` | Bearer | Send a message |
| GET | `/{id}/personal/auto-reply` | Bearer | Get auto-reply config |
| PUT | `/{id}/personal/auto-reply` | Bearer | Update auto-reply config |

## Environment Variables

| Variable | Service | Purpose |
|----------|---------|---------|
| `MESSENGER_VERIFY_TOKEN` | social-api | Webhook GET verification |
| `FACEBOOK_APP_SECRET` | social-api | Webhook POST signature |
| `BROWSER_BRIDGE_URL` | social-api, worker | Browser bridge URL |
| `MESSENGER_SIDECAR_URL` | social-api | Sidecar dispatch URL |
| `CLOUDFLARE_API_TOKEN` | sidecar, worker | AI auto-reply (Workers AI) |
| `CLOUDFLARE_ACCOUNT_ID` | sidecar, worker | AI auto-reply (account) |
| `DMR_BASE_URL` | sidecar, worker | DMR fallback URL |
| `SOCIAL_ADMIN_EMAIL` | sidecar, worker | Admin auth to social-api |
| `SOCIAL_ADMIN_PASSWORD` | sidecar, worker | Admin auth to social-api |

## Security

- Page tokens stored encrypted in `SocialAccount.meta_data.page_token`
- Auto-reply configs in `SocialAccount.meta_data` (JSONB)
- Webhook POST verified via HMAC-SHA256 with `FACEBOOK_APP_SECRET`
- Personal Messenger requires authenticated noVNC browser session
- All API endpoints require Bearer token (except webhook GET/POST)
- Admin user has unrestricted access to all Messenger features
- No secrets exposed in MCP tool responses

## Limitations

| Limitation | Reason | Mitigation |
|------------|--------|------------|
| Personal Messenger has no webhooks | Meta only supports Pages | Celery polling every 2 min |
| E2EE threads may not have thread_id | URL format differs | Skip threads without numeric ID |
| Browser session can expire | Cookie-based auth | noVNC re-login required |
| DOM selectors are fragile | Facebook UI changes | Multiple fallback selectors |
| 24-hour messaging window (Pages) | Meta policy | Message tags for outside window |
| Mercury endpoints decommissioned | Facebook removed them | Browser automation only |
| Rate limit: 10 profile calls/10 min | Meta limit | Batch updates, `rate_limited` status |
