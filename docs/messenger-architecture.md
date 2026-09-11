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
│   │ /messenger   │────────▶│              │◀────────│  15 Messenger│     │
│   │              │         │              │         │  tools       │     │
│   │ Page inbox   │         │  /api/v1/    │         │              │     │
│   │ Personal     │         │  messenger/* │         │  Page (9)    │     │
│   │ inbox        │         │              │         │  Personal (4)│     │
│   │ Auto-reply   │         │              │         │  AutoRply (2)│     │
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
│   │                  │  │                  │  │  Queue: messenger│      │
│   │  • /{page}/      │  │  • Chromium       │  │                  │      │
│   │    messages      │  │  • facebook.com/  │  │  poll_personal_  │      │
│   │  • /{page}/      │  │    messages      │  │  messenger()     │      │
│   │    message_      │  │  • E2EE threads  │  │                  │      │
│   │    subscriptions │  │  • PIN handling   │  │  Polls personal  │      │
│   │  • /me/          │  │                  │  │  conversations   │      │
│   │    conversations │  │  (no API key)    │  │  → AI reply      │      │
│   └──────────────────┘  └──────────────────┘  │  → browser send │      │
│                                                └──────────────────┘      │
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
       │ AI Fallback Chain (language-aware):
       ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  1. Detect language (Greek Unicode range 0x0370-0x03FF, 0x1F00-1FFF) │
  │                                                                  │
  │  ┌─ Greek text ──────────────────────────────────────────────┐    │
  │  │  Cloudflare Workers AI (Llama 3.1 8B)  ← handles Greek   │    │
  │  │    ↓ on failure                                          │    │
  │  │  DMR (Llama 3.2 / Qwen3 8B, local)                       │    │
  │  │    ↓ on failure                                          │    │
  │  │  Static fallback text (with bot disclosure)              │    │
  │  └──────────────────────────────────────────────────────────┘    │
  │                                                                  │
  │  ┌─ English / other ─────────────────────────────────────────┐    │
  │  │  DMR (Llama 3.2, local, free, private)  ← primary         │    │
  │  │    ↓ on failure                                          │    │
  │  │  Cloudflare Workers AI (Llama 3.1 8B)                    │    │
  │  │    ↓ on failure                                          │    │
  │  │  Static fallback text (with bot disclosure)              │    │
  │  └──────────────────────────────────────────────────────────┘    │
  │                                                                  │
  │  DMR config (RTX 3070 8GB VRAM):                                │
  │    llama3.2:   ctx=8192, flash-attn=on, n-gpu-layers=99         │
  │    qwen3:8b:   ctx=8192, reasoning=on, flash-attn=on             │
  │    smollm2:    ctx=2048 (intent detection)                       │
  │    qwen3-emb:  embedding mode (RAG)                             │
  └──────────────────────────────────────────────────────────────────┘
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
│  │  Queue: messenger                                           │   │
│  │  Task: app.worker.tasks.personal_messenger                   │   │
│  │        .poll_personal_messenger                             │   │
│  └──────────────────────┬──────────────────────────────────────┘   │
│                         ▼                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  1. Find Facebook personal accounts with auto-reply enabled │   │
│  │  2. For each account:                                       │   │
│  │     a. Fetch conversations via browser bridge               │   │
│  │     b. Filter: only thread_id AND unread=True              │   │
│  │     c. For each unread conversation:                        │   │
│  │        - Read recent messages                               │   │
│  │        - Find last inbound message ("them")                  │   │
│  │        - Check if already replied (seen tracking)            │   │
│  │        - Check cooldown (Redis, 5 min)                      │   │
│  │        - Check human handoff pause                           │   │
│  │        - Detect intent (DMR→CF fallback)                    │   │
│  │        - Retrieve brand context (RAG) + memory              │   │
│  │        - Generate reply (Greek→CF, English→DMR)             │   │
│  │        - Send typing indicator (natural delay)              │   │
│  │        - Send reply via browser bridge                       │   │
│  │        - Store in memory (ChromaDB) + set cooldown          │   │
│  │  3. Update last_checked timestamp                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  State tracking (SocialAccount.meta_data):                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  personal_messenger_auto_reply:                             │   │
│  │    enabled: true                                            │   │
│  │    system_prompt: "You are {name}..."                       │   │
│  │    model: "ai/qwen3:8b-q4_K_M"  (DMR)                      │   │
│  │    fallback_text: "Thanks for your message!"                │   │
│  │    max_tokens: 250                                          │   │
│  │    cooldown_seconds: 300                                     │   │
│  │                                                             │   │
│  │  personal_messenger_seen:                                  │   │
│  │    {thread_id: last_replied_message_text}                   │   │
│  │                                                             │   │
│  │  personal_messenger_last_checked: "2026-09-11T01:53:..."     │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Bot Architecture (messenger_chatbot.py)

The bot engine is shared between Page webhook processing (sidecar) and
personal polling (Celery). It provides intent detection, conversation
memory, brand RAG, per-thread cooldowns, human handoff, and bot disclosure.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Messenger Bot Engine                              │
│                    (app/services/messenger_chatbot.py)               │
│                                                                     │
│  ┌─ Intent Detection ──────────────────────────────────────────┐   │
│  │  Categories: business, personal, question, spam, greeting    │   │
│  │  Provider: DMR (smollm2 / llama3.2) → CF fallback             │   │
│  │  Used to: skip spam/greetings (configurable), tune prompt    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Conversation Memory (ChromaDB) ─────────────────────────────┐   │
│  │  Collection: messenger_memory                                │   │
│  │  Stores: last 10 messages per thread (user + assistant)      │   │
│  │  Retrieval: thread_id metadata filter, last N messages      │   │
│  │  Purpose: context-aware replies (remembers prior context)   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Brand Knowledge RAG (ChromaDB) ────────────────────────────┐   │
│  │  Collection: messenger_brand_knowledge                      │   │
│  │  Source: Brand DNA API (voice, pillars, positioning)        │   │
│  │  Indexing: POST /{id}/personal/index-brand                  │   │
│  │  Retrieval: semantic search on incoming message             │   │
│  │  Purpose: brand-consistent voice and accurate answers      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Redis State (DB 1) ────────────────────────────────────────┐   │
│  │  messenger:cooldown:{acct}:{thread}  TTL=cooldown_seconds    │   │
│  │  messenger:paused:{acct}:{thread}     human handoff flag    │   │
│  │  messenger:config:{acct}:{thread}     per-thread overrides  │   │
│  │  messenger:disclosed:{acct}:{thread}  bot disclosure state  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Prompt Construction ───────────────────────────────────────┐   │
│  │  1. Base system prompt (per-thread or default)               │   │
│  │  2. Intent-specific guidance                                 │   │
│  │  3. Brand context (RAG results)                              │   │
│  │  4. Last 5 messages from memory                              │   │
│  │  5. Same-language response instruction                       │   │
│  │  6. First-contact bot disclosure (if not yet disclosed)      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Reply Generation (language-aware) ────────────────────────┐   │
│  │  Greek text → Cloudflare Workers AI (Llama 3.1 8B)          │   │
│  │  English    → DMR (Llama 3.2, local)                        │   │
│  │  Fallback   → other provider → static text                  │   │
│  │  Disclosure → "🤖 Auto-reply:" prefix on first contact      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Typing Indicator ─────────────────────────────────────────┐   │
│  │  Delay scales with reply length: ~1s per 50 chars           │   │
│  │  Min 1.5s, max 5s (natural feel)                             │   │
│  │  3s sleep between processed replies (rate safety)           │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

**Defaults:**
- `DEFAULT_COOLDOWN_SECONDS = 300` (5 min between replies per thread)
- `DEFAULT_MEMORY_MESSAGES = 10` (last 10 messages in ChromaDB)
- Personal polling interval: 120 seconds (Celery beat)
- Static fallback includes human handoff language

**Bot disclosure:** On first contact, the bot prefixes the reply with
`🤖 Auto-reply:` to comply with platform transparency policies. The
disclosure state is tracked in Redis (`messenger:disclosed:{acct}:{thread}`)
and persists per thread.

**Human handoff:** A thread can be paused via
`POST /{id}/bot/pause-thread/{tid}` or `POST /{id}/personal/threads/{tid}/pause`.
While paused, the bot skips all inbound messages for that thread. Resume
via the corresponding resume endpoint. Paused threads are listed in the
Bot Builder UI with a Resume button.

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
│  │ social-     │  │ social-     │  │ messenger-  │  │ celery-beat│ │
│  │ worker-     │  │ worker-     │  │ sidecar     │  │            │ │
│  │ default     │  │ messenger   │  │ :9230       │  │ Scheduler  │ │
│  │             │  │             │  │             │  │            │ │
│  │ Celery      │  │ Celery      │  │ FastAPI     │  │ beat_      │ │
│  │ default     │  │ messenger   │  │ sidecar     │  │ schedule   │ │
│  │             │  │             │  │             │  │            │ │
│  │ analytics,  │  │ poll_       │  │ AI auto-    │  │ 120s:      │ │
│  │ workflows   │  │ personal_   │  │ reply       │  │ poll       │ │
│  │             │  │ messenger   │  │ (Page)      │  │ personal   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ browser-    │  │ redis       │  │ social-     │  │ warp-proxy │ │
│  │ novnc       │  │ :6379       │  │ postgres    │  │ :1080       │ │
│  │ :6080 (VNC)│  │             │  │             │  │             │ │
│  │ :9223 (CDP)│  │ Celery      │  │ social_     │  │ Cloudflare  │ │
│  │             │  │ broker      │  │ automation  │  │ WARP SOCKS5 │ │
│  │ Chromium    │  │             │  │ DB          │  │ (free proxy)│ │
│  │ facebook.com│  │             │  │             │  │             │ │
│  │ sessions    │  │             │  │             │  │             │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐                                                    │
│  │ DMR (host)  │  Host-level Docker engine (not a Compose service)  │
│  │ :12434      │  Llama 3.2, Qwen3 8B, Qwen3-VL, Qwen3-embedding    │
│  │             │  OpenAI + Anthropic + Ollama compatible APIs        │
│  └─────────────┘                                                    │
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
     │                    │                    │                 Language-aware:
     │                    │                    │                 Greek → CF Workers AI
     │                    │                    │                 English → DMR (Llama 3.2)
     │                    │                    │                 → other provider
     │                    │                    │                 → static fallback
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
     │                    │                    │  6. Find last "them" │
     │                    │                    │  7. Check seen       │
     │                    │                    │  8. Check cooldown   │
     │                    │                    │     (Redis 5 min)    │
     │                    │                    │  9. Check paused    │
     │                    │                    │     (human handoff) │
     │                    │                    │ 10. Detect intent   │
     │                    │                    │     (DMR→CF)         │
     │                    │                    │ 11. Retrieve brand  │
     │                    │                    │     context (RAG)   │
     │                    │                    │ 12. AI generate     │
     │                    │                    │───────────────────▶│
     │                    │                    │     Greek → CF      │
     │                    │                    │     English → DMR   │
     │                    │                    │     → other → static│
     │                    │                    │ 13. Reply text      │
     │                    │                    │◀───────────────────│
     │                    │                    │ 14. Typing delay    │
     │                    │                    │ 15. Send via bridge │
     │                    │◀───────────────────│                    │
     │                    │ 16. Type + Enter   │                    │
     │  17. Reply appears │                    │                    │
     │◀───────────────────│                    │                    │
     │                    │                    │ 18. Store memory   │
     │                    │                    │     (ChromaDB)      │
     │                    │                    │ 19. Set cooldown   │
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
| POST | `/{id}/personal/threads/{tid}/pause` | Bearer | Pause bot for a thread (human handoff) |
| POST | `/{id}/personal/threads/{tid}/resume` | Bearer | Resume bot for a thread |
| GET | `/{id}/personal/threads/{tid}/config` | Bearer | Get per-thread bot config |
| PUT | `/{id}/personal/threads/{tid}/config` | Bearer | Set per-thread bot config |
| GET | `/{id}/personal/threads/{tid}/memory` | Bearer | Get conversation memory |
| POST | `/{id}/personal/index-brand` | Bearer | Index brand DNA into ChromaDB |

### Bot Builder (Unified, both account types)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/{id}/bot/create` | Bearer | Create a bot (personality, language, business hours) |
| GET | `/{id}/bot` | Bearer | Get bot config + status |
| PUT | `/{id}/bot` | Bearer | Update bot config |
| POST | `/{id}/bot/activate` | Bearer | Activate bot (start auto-replying) |
| POST | `/{id}/bot/deactivate` | Bearer | Deactivate bot (pause auto-reply) |
| POST | `/{id}/bot/pause-thread/{tid}` | Bearer | Pause bot for a thread (human handoff) |
| POST | `/{id}/bot/resume-thread/{tid}` | Bearer | Resume bot for a thread |
| GET | `/{id}/bot/personalities` | Bearer | List personality presets |

## Environment Variables

| Variable | Service | Purpose |
|----------|---------|---------|
| `MESSENGER_VERIFY_TOKEN` | social-api | Webhook GET verification |
| `FACEBOOK_APP_SECRET` | social-api | Webhook POST signature |
| `BROWSER_BRIDGE_URL` | social-api, worker-messenger | Browser bridge URL |
| `MESSENGER_SIDECAR_URL` | social-api | Sidecar dispatch URL |
| `CLOUDFLARE_API_TOKEN` | sidecar, worker-messenger | AI auto-reply (Workers AI) |
| `CLOUDFLARE_ACCOUNT_ID` | sidecar, worker-messenger | AI auto-reply (account) |
| `DMR_URL` | sidecar, worker-messenger | DMR base URL (`http://host.docker.internal:12434/engines/llama.cpp/v1`) |
| `DMR_TEXT_MODEL` | sidecar, worker-messenger | Primary text model (`ai/llama3.2`) |
| `DMR_TINY_MODEL` | sidecar, worker-messenger | Intent detection model (`ai/smollm2`) |
| `DMR_EMBEDDING_MODEL` | sidecar, worker-messenger | Embeddings for RAG (`ai/qwen3-embedding`) |
| `SOCIAL_ADMIN_EMAIL` | sidecar, worker-messenger | Admin auth to social-api |
| `SOCIAL_ADMIN_PASSWORD` | sidecar, worker-messenger | Admin auth to social-api |

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
