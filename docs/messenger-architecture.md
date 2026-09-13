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
  │  │  Cloudflare Workers AI (Llama 3.1 8B)  ← primary         │    │
  │  │    ↓ on failure                                          │    │
  │  │  DMR (Llama 3.2 / Qwen3 8B, local, free, private)         │    │
  │  │    ↓ on failure                                          │    │
  │  │  Static fallback text (with bot disclosure)              │    │
  │  └──────────────────────────────────────────────────────────┘    │
  │                                                                  │
  │  Deterministic safeguards (before LLM):                          │
  │    Pricing questions → hardcoded response (no LLM)               │
  │    Brand voice rules → injected from Brand system API            │
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
│  │        - Check deterministic pricing safeguard              │   │
│  │        - Inject brand voice rules (Brand system API)        │   │
│  │        - Generate reply (CF Workers AI → DMR → static)      │   │
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
│  │  2. Brand voice rules (from Brand system API, 5-min cache)  │   │
│  │     - Banned phrases (synergy, leverage, cutting-edge, ...) │   │
│  │     - Preferred phrases (Clear skies. Zero friction., ...)  │   │
│  │     - Euro pricing enforcement (never USD)                  │   │
│  │     - Bilingual Greek/English language matching              │   │
│  │     - Bot disclosure requirement                            │   │
│  │  3. Intent-specific guidance                                 │   │
│  │  4. Brand context (RAG results)                              │   │
│  │  5. Last 5 messages from memory                              │   │
│  │  6. Same-language response instruction                       │   │
│  │  7. First-contact bot disclosure (if not yet disclosed)      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Deterministic Safeguards ────────────────────────────────┐   │
│  │  Pricing questions intercepted before LLM:                  │   │
│  │    Keywords: how much, price, cost, πόσο, τιμή, κόστος, ... │   │
│  │    Response: "Get started at cloudless.gr for a free audit!" │   │
│  │    (Greek and English variants, prevents price hallucination) │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ Reply Generation (unified, Cloudflare-first) ────────────┐   │
│  │  1. Deterministic pricing safeguard (keyword detection)    │   │
│  │     → hardcoded response (prevents LLM price hallucination) │   │
│  │  2. Cloudflare Workers AI (Llama 3.1 8B) — primary       │   │
│  │     → DMR (Qwen3 8B, local) — fallback                   │   │
│  │     → Static text — final fallback                        │   │
│  │  Disclosure → "🤖 Auto-reply:" prefix on first contact      │   │
│  │  Analytics → track_bot_reply() logs to:                    │   │
│  │     - ai_usage_logs (provider, model, latency, success)    │   │
│  │     - analytics_events (bot_reply event with metadata)     │   │
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

**Bot analytics:** Every bot reply is tracked via `track_bot_reply()` to two
tables:
- `ai_usage_logs` — provider, model, latency, success/failure (cost tracking)
- `analytics_events` — `bot_reply` event with metadata: thread_id, provider,
  model, success, error, intent, guardrail, language, reply_length, latency_ms

All 5 return paths are instrumented (pricing guardrail, CF success/failure,
DMR success/failure). All 6 polling tasks pass `team_id=account.team_id` so
events are scoped to the correct team.

**Analytics API endpoints:**
- `GET /api/v1/analytics/bots/summary` — bot reply metrics from PostgreSQL
  (total replies, success rate, guardrail triggers, Greek/English, latency,
  per-provider/account/day).
- `GET /api/v1/analytics/bots/cloudflare-ai` — Workers AI usage from
  Cloudflare GraphQL Analytics API (free): neurons, requests, free-tier
  remaining, per-model, per-day.
- `GET /api/v1/analytics/bots/cloudflare-overview` — combined Cloudflare
  analytics: Workers AI, Workers, R2, D1, KV, Vectorize in one response.

**Cloudflare Analytics client** (`app/services/cf_analytics.py`):
Queries the free Cloudflare GraphQL Analytics API using the existing
`CLOUDFLARE_API_TOKEN`. Datasets:
- `aiInferenceAdaptiveGroups` — Workers AI inference (tokens, requests)
- `workersInvocationsAdaptive` — Worker invocations (CPU, errors)
- `r2OperationsAdaptiveGroups` — R2 operations by bucket
- `d1QueriesAdaptiveGroups` — D1 query counts per database
- `kvOperationsAdaptiveGroups` — KV read/write/delete operations
- `vectorizeQueriesAdaptiveGroups` — Vectorize query counts per index

**Frontend analytics dashboard** (`app/(dashboard)/analytics/page.tsx`):
The `/analytics` page renders two new sections below the existing post/follower/engagement metrics:
- **Bot Reply Analytics** — KPI cards (total replies, success rate, guardrail triggers, pricing guardrails, Greek/English, avg latency, failures), provider breakdown with icons (Cloudflare/DMR/deterministic), and daily activity bar chart (replies/errors/guardrails stacked).
- **Cloudflare Infrastructure Analytics** — KPI cards for Workers AI (requests, neurons, free-tier remaining), Workers (requests, errors), R2 operations, D1 queries, KV operations; Workers AI model breakdown, Worker script breakdown, R2 bucket breakdown, D1 database breakdown.

Frontend data flow: `src/types/index.ts` (types) → `src/services/api.ts` (API methods) → `src/hooks/useQueries.ts` (React Query hooks with 30s/60s refetch) → `app/(dashboard)/analytics/page.tsx` (UI). E2E tests in `tests/analytics.test.ts`.

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
     │                    │                    │                 Deterministic pricing safeguard
     │                    │                    │                 Brand voice injection (Brand API)
     │                    │                    │                 Unified routing:
     │                    │                    │                 CF Workers AI (primary) → DMR → static
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
     │                    │                    │ 12. Pricing safeguard│
     │                    │                    │     Brand voice inject│
     │                    │                    │ 13. AI generate     │
     │                    │                    │───────────────────▶│
     │                    │                    │     CF Workers AI   │
     │                    │                    │     → DMR → static  │
     │                    │                    │ 14. Reply text      │
     │                    │                    │◀───────────────────│
     │                    │                    │ 15. Typing delay    │
     │                    │                    │ 16. Send via bridge │
     │                    │◀───────────────────│                    │
     │                    │ 17. Type + Enter   │                    │
     │  18. Reply appears │                    │                    │
     │◀───────────────────│                    │                    │
     │                    │                    │ 19. Store memory   │
     │                    │                    │     (ChromaDB)      │
     │                    │                    │ 20. Set cooldown   │
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

---

## Instagram DM Bot (Browser Bridge Fallback)

### Architecture

Instagram DMs use a **two-tier fallback** architecture:

1. **Graph API (primary)** — requires Meta App Review for `instagram_business_manage_messages` permission. Currently returns `(#3) Application does not have the capability` for the Cloudless app.
2. **Browser Bridge (fallback)** — uses the logged-in Instagram web session in `browser-novnc` to read and send DMs via the Instagram web API (`www.instagram.com/api/v1/direct_v2/`).

### Browser Bridge DM Methods

`BrowserBridgeClient` provides three Instagram DM methods:

| Method | Description |
|--------|-------------|
| `get_instagram_dm_conversations()` | Reads inbox via `GET /api/v1/direct_v2/inbox/` from browser context |
| `get_instagram_dm_messages(thread_id)` | Reads thread messages via `GET /api/v1/direct_v2/threads/{id}/` |
| `send_instagram_dm_message(recipient_id, text)` | Sends DM via **UI interaction** (navigate to thread, type, press Enter) |

### Send Method: UI Interaction

Instagram's web API POST endpoint (`/api/v1/direct_v2/threads/broadcast/text/`) returns an opaque redirect when called from the browser context (`fetch()` with `redirect: "manual"` returns `opaqueredirect`). This is a security measure by Instagram.

The send method uses **UI interaction** instead:
1. Navigate to `https://www.instagram.com/direct/inbox/`
2. Fetch inbox to find the thread_id for the recipient
3. Navigate to the thread page
4. Find the contenteditable message input
5. Type the text using `document.execCommand('insertText')`
6. Click the Send button or press Enter

This mirrors the approach used for Threads, Twitter/X, and TikTok DMs.

### Sender Detection

Instagram's web API returns `user_id` as the **viewer's ID** for inbox items, not the actual sender. The correct field is:

- `is_sent_by_viewer: true` → outbound (sent by the account, skip)
- `is_sent_by_viewer: false` → inbound (sent by another user, reply)

### Worker Flow

```
Graph API (conversations) → fails with (#3)
    ↓ fallback
Browser Bridge (inbox via web API fetch)
    ↓
For each conversation:
    1. Read messages via browser bridge
    2. Find last inbound message (is_sent_by_viewer: false)
    3. Check seen tracking + cooldown
    4. Generate AI reply (Cloudflare Workers AI → DMR → static)
    5. Send reply via UI interaction (navigate, type, Enter)
    6. Check send result — don't mark as seen if send failed
    7. Store in conversation memory (ChromaDB)
    8. Set cooldown
```

### Conversational Steering

The bot is designed to **continue the conversation** and **steer customers** toward the next step:

- **Intent detection** classifies messages as business, personal, question, spam, or greeting
- **Intent guidance** in the system prompt tells the bot how to respond per intent:
  - Business: ask follow-up questions, mention services, steer to booking
  - Greeting: introduce as bot, ask what they're interested in
  - Question: answer, then ask what they need help with
- **Conversation memory** (ChromaDB) stores last N messages for context
- **Brand context** (RAG) retrieves relevant brand knowledge
- **Pricing guardrail** intercepts pricing questions with a deterministic response that asks what service they're interested in
- **System prompt** instructs the bot to always end with a question and guide toward booking/consultation
- **Brand voice injection** enforces banned phrases, euro pricing, bilingual language matching

### Browser Bridge Result Extraction

All browser bridge `evaluate()` calls return `{"status": "ok", "result": <value>}`. DM methods must extract the `result` field:

```python
result = response.get("result", response) if isinstance(response, dict) else response
return result if isinstance(result, dict) else {"error": str(result)}
```

This applies to all platforms: Threads, Twitter/X, TikTok, and Instagram.

### UUID Serialization Fix

`track_inference()` in `usage_tracker.py` sanitizes `meta_data` to convert UUID values to strings before JSONB serialization, preventing the "Object of type UUID is not JSON serializable" error.

### Limitations

| Limitation | Reason | Mitigation |
|------------|--------|------------|
| Graph API unavailable | Needs Meta App Review | Browser bridge fallback |
| Browser session expires | Cookie-based auth | Re-login via noVNC |
| One Instagram account at a time | Shared browser session | Login to the account you want to process |
| Other workers can navigate browser away | Shared browser-novnc | Stop celery-beat during testing |
| UI selectors are fragile | Instagram UI changes | Multiple fallback selectors |
| Send via API POST blocked | Opaque redirect | UI interaction (type + Enter) |

---

## Conversational Steering & Language Matching (v2)

### Overview

The bot is designed to **continue the conversation** and **steer customers** toward the correct direction. It replies to ALL user queries (not just the last message) and matches the user's language exactly.

### Multi-Message Context

The worker collects **ALL unread inbound messages** in a conversation, not just the last one. If there are multiple unread messages, they are combined into a single context string so the bot can address each query:

```python
# Collect all inbound messages (not sent by viewer)
inbound_messages = []
for msg in reversed(messages):
    if not is_outbound and msg.get(text_field):
        inbound_messages.append(msg_text)

# Combine all unread messages for context
if len(inbound_messages) > 1:
    text = "\n".join(inbound_messages)
    logger.info("Instagram DM: %d unread messages from '%s', combining for context", ...)
```

### Language Detection & Enforcement

The bot detects the user's language programmatically using `_is_greek_message()` (checks for Greek Unicode characters) and enforces it in **three places**:

1. **System prompt** — `CRITICAL LANGUAGE RULE: The user's message is in {detected_lang}. You MUST reply ONLY in {detected_lang}.`
2. **User message** — `[Reply in English only] i need help with my infra` (prepended to the user's actual message)
3. **DMR fallback** — Same language instruction passed to the local DMR model

This three-layer enforcement ensures the 8B model (Llama 3.1) doesn't drift to Greek when the user writes in English, or vice versa.

### Intent-Based Steering

The bot classifies the user's intent (business, personal, question, spam, greeting) and uses intent-specific guidance:

| Intent | Steering Behavior |
|--------|-------------------|
| **business** | Ask follow-up questions, mention services, steer to booking |
| **greeting** | Introduce as bot, ask what they're interested in |
| **question** | Answer, then ask what they need help with |
| **personal** | Be friendly, gently steer to Cloudless if business need arises |
| **spam** | Respond politely but briefly |

### Conversational System Prompt

The business account bot config includes explicit steering rules:

```
CONVERSATIONAL STEERING:
- If the user has multiple questions, address EACH one.
- If they mention a problem, ask a follow-up to understand it better.
- If they ask about services, explain what Cloudless offers and ask what they need.
- If they ask about pricing, steer to cloudless.gr for a free audit.
- If they're ready to proceed, suggest booking a consultation at cloudless.gr.
- If they're just chatting, be friendly and ask what they're interested in.
- ALWAYS end with a question to keep the conversation going.
- Be conversational - this is a chat, not a one-time reply.
- Remember context from previous messages in the conversation.
```

### Pricing Guardrail (Deterministic)

Pricing questions are intercepted **before** the LLM to prevent fabricated prices. The deterministic response asks what service they're interested in:

- **English**: `🤖 Hi! I'm the Cloudless bot. Pricing depends on your needs — every project is different. You can start with a free audit at cloudless.gr. What kind of service are you interested in? (cloud, automation, social media)`
- **Greek**: `🤖 Γεια! Είμαι το Cloudless bot. Η τιμή εξαρτάται από τις ανάγκες σας — κάθε έργο είναι διαφορετικό. Μπορείτε να ξεκινήσετε με δωρεάν αξιολόγηση στο cloudless.gr. Τι είδους υπηρεσία σας ενδιαφέρει; (cloud, αυτοματοποίηση, social media)`

### Conversation Memory (ChromaDB)

The bot stores the last N messages per conversation in ChromaDB. This context is injected into the system prompt so the bot remembers previous messages:

```python
memory = await get_conversation_memory(account_id, thread_id)
if memory:
    memory_text = "\n".join(f"{'You' if m['sender'] == 'me' else 'Them'}: {m['text'][:100]}" for m in memory[-5:])
    enhanced_prompt += f"\n\nRecent conversation:\n{memory_text}"
```

### Brand Context (RAG)

The bot retrieves relevant brand knowledge from ChromaDB using embeddings, providing grounded context about Cloudless services:

```python
brand_context = await retrieve_brand_context(text)
if brand_context:
    enhanced_prompt += f"\n\nBrand context:\n{brand_context}"
```

### Brand Voice Injection

Brand voice rules (banned phrases, preferred phrases, euro pricing, language matching, bot disclosure) are fetched from the Brand system API and injected into the system prompt. Cached for 5 minutes.

### Test Results

**10/10 bot reply tests passed:**

| Test | Language | Intent | Reply Language | Ends with Question |
|------|----------|--------|----------------|-------------------|
| English greeting | English | greeting | English ✅ | ✅ |
| English pricing | English | business | English ✅ | ✅ |
| English multi-query | English | business | English ✅ | ✅ |
| English services | English | question | English ✅ | ✅ |
| English ready to book | English | business | English ✅ | ✅ |
| Greek greeting | Greek | greeting | Greek ✅ | ✅ |
| Greek pricing | Greek | business | Greek ✅ | ✅ |
| Greek multi-query | Greek | business | Greek ✅ | ✅ |
| Greek services | Greek | question | Greek ✅ | ✅ |
| Greek ready to book | Greek | business | Greek ✅ | ✅ |

**All platform polls: 0 errors**

| Platform | Accounts | Replies | Errors |
|----------|---------|---------|--------|
| Instagram | 1 | 0* | 0 |
| Threads | 1 | 0 | 0 |
| Twitter | 1 | 0 | 0 |
| TikTok | 1 | 0 | 0 |
| Personal Messenger | 1 | 0 | 0 |
| LinkedIn | 2 | 0 | 0 |

*Instagram reply was sent in a previous poll (confirmed in DM thread)

---

## Browser Bridge Orchestrator (v3)

### Problem

The browser-novnc container (port 9223) has a **single shared Chromium
browser session**. Multiple Celery workers (Instagram, Threads, Twitter,
TikTok, personal Messenger) all use this browser to read/send DMs via
web APIs or UI interaction.

When worker A navigates to `facebook.com` and worker B tries to fetch
`instagram.com` API, worker B gets `Failed to fetch` because the browser
is on a different origin. This caused DM polls to silently fail — the
bot would not reply to customer messages.

### Solution: Redis-based Distributed Lock with Fair Scheduling

The `BrowserBridgeOrchestrator` (`app/services/browser_orchestrator.py`)
coordinates browser access across all workers:

```
Worker A (Instagram) → browser_session("instagram", bridge)
    → Acquires Redis lock (SET NX + EX)
    → Navigates to instagram.com
    → Reads DMs, sends reply
    → Releases lock

Worker B (Facebook) → browser_session("facebook", bridge)
    → Waits in fair queue (Redis LIST)
    → Acquires lock when A releases
    → Navigates to facebook.com
    → Does its work
    → Releases lock
```

### Key components

| Component | Redis key | Purpose |
|-----------|-----------|---------|
| Global lock | `browser-bridge:lock` | Only one worker uses the browser at a time |
| Fair queue | `browser-bridge:queue` | Workers wait in FIFO order |
| Platform tracker | `browser-bridge:platform` | Which platform currently holds the lock |

### Timing

| Setting | Value | Rationale |
|---------|-------|----------|
| Lock timeout (auto-release) | 90s | Enough for any browser interaction (navigate + sleep + fetch + send) |
| Max wait time | 60s | Prevents workers from blocking too long |
| Retry delay | 0.3s | Balance between responsiveness and Redis load |

### Usage in workers

All messenger workers wrap their browser bridge calls with the orchestrator:

```python
from app.services.browser_orchestrator import browser_session

# Read conversations
async with browser_session("instagram", bridge) as b:
    convos = await b.get_instagram_dm_conversations()

# Read messages
async with browser_session("instagram", bridge) as b:
    msgs = await b.get_instagram_dm_messages(thread_id)

# Send reply
async with browser_session("instagram", bridge) as b:
    await b.send_instagram_dm_message(recipient_id, reply_text)
```

### Platforms using the orchestrator

| Platform | Worker | Platform key |
|----------|--------|--------------|
| Instagram | `instagram_messenger.py` | `instagram` |
| Threads | `threads_messenger.py` | `threads` |
| Twitter/X | `twitter_messenger.py` | `twitter` |
| TikTok | `tiktok_messenger.py` | `tiktok` |
| Personal Messenger | `personal_messenger.py` | `facebook` |

### Live verification

```
Browser orchestrator: instagram acquired lock after 16.2s wait
Instagram auto-reply sent to 't_baltzakis'
Instagram DM poll complete: {'accounts_checked': 1, 'replies_sent': 1, 'errors': 0}
Browser orchestrator: facebook acquired lock after 9.0s wait
```

The orchestrator ensures:
1. Only one worker uses the browser at a time ✅
2. Workers wait their turn in fair FIFO order ✅
3. The Instagram bot replies automatically when a customer sends a DM ✅
4. No more "Failed to fetch" errors from concurrent navigation ✅

### Admin/debug commands

```bash
# Check which platform holds the lock
redis-cli GET browser-bridge:lock

# Check queue length
redis-cli LLEN browser-bridge:queue

# Force-release the lock (debug only)
docker compose exec -T social-api python -c "
import asyncio
from app.services.browser_orchestrator import force_release_lock
asyncio.run(force_release_lock())
"
```
