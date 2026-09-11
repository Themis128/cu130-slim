# SocialAuto Messenger Bot Guide

A complete guide to creating, configuring, and managing AI-powered
auto-reply bots for both **personal** and **business** Facebook Messenger
accounts in SocialAuto.

---

## Table of Contents

1. [Overview](#overview)
2. [Account Types](#account-types)
3. [Creating a Bot](#creating-a-bot)
4. [Personality Presets](#personality-presets)
5. [Configuration Reference](#configuration-reference)
6. [Activating & Deactivating](#activating--deactivating)
7. [Human Handoff (Pause/Resume)](#human-handoff-pauseresume)
8. [How the Bot Works](#how-the-bot-works)
9. [Mobile App (Android/iOS) Behavior](#mobile-app-androidios-behavior)
10. [Best Practices Applied](#best-practices-applied)
11. [API Reference](#api-reference)
12. [Troubleshooting](#troubleshooting)

---

## Overview

The SocialAuto Messenger Bot is an AI-powered auto-reply system that
responds to incoming Messenger messages on your behalf. It uses:

- **Docker Model Runner (DMR)** with Llama 3.2 (local, free) as primary inference for English
- **Cloudflare Workers AI** with Llama 3.1 8B as primary for Greek and fallback for English
- **ChromaDB** for conversation memory and brand knowledge RAG
- **Redis DB 1** for cooldowns, pause state, and disclosure tracking
- **PostgreSQL** for persistent bot configuration

The bot detects intent (business, personal, greeting, spam, question),
retrieves brand context, builds a conversation-aware prompt, generates a
reply using language-aware routing (Greek→Cloudflare, English→DMR), and
sends it — all within a few seconds.

---

## Account Types

SocialAuto supports two types of Facebook Messenger accounts:

### Business Page (Page account)

| Aspect | Details |
|--------|---------|
| **API** | Meta Messenger Platform (Graph API v25.0) |
| **Authentication** | Page Access Token (`pages_messaging` scope) |
| **Webhooks** | Real-time push via webhook sidecar (port 9230) |
| **Setup** | One-time Page subscription + Messenger Profile setup |
| **Reply delivery** | Graph API `/me/messages` endpoint |
| **Features** | Greeting text, Get Started button, persistent menu, ice breakers, quick replies |
| **Limitations** | 24-hour messaging window, requires Page admin access |

### Personal Account (User account)

| Aspect | Details |
|--------|---------|
| **API** | Browser bridge (Playwright + CDP + noVNC) |
| **Authentication** | Browser session via noVNC (port 6080/9223) |
| **Polling** | Celery task every 120 seconds (no webhooks for personal) |
| **Setup** | Log in via noVNC browser session (one-time) |
| **Reply delivery** | Browser automation (types in Messenger web UI) |
| **Features** | E2EE + regular thread support, intent detection, brand RAG |
| **Limitations** | Requires active browser session, no official API from Meta |

> **Meta does not provide an official API for personal Messenger accounts.**
> The browser bridge is the only way to automate personal Messenger. This
> means the browser session must stay logged in for the bot to work.

---

## Creating a Bot

### Via the Web UI

1. Navigate to **Messenger** in the SocialAuto dashboard
   (`https://social.cloudless.gr/messenger`)
2. Select a Facebook account from the dropdown
   - Personal accounts are grouped under "Personal"
   - Business Pages are grouped under "Business Pages"
3. The **Bot Builder** card appears below the account selector
4. If no bot exists, click **Create Bot**
5. Fill in the form:
   - **Bot Name**: Display name (e.g., "Cloudless Assistant")
   - **Personality**: Choose from 5 presets (see below)
   - **Language**: Auto (match incoming), English, or Greek
   - **Business Hours** (optional): UTC start/end times
   - **Custom System Prompt** (optional): Overrides personality preset
6. Click **Create Bot**

The bot is created **active** by default. It will start auto-replying on
the next poll (personal) or webhook event (business Page).

### Via the API

```bash
# Get auth token
TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=your@email.com" \
  --data-urlencode "password=yourpassword" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create bot
curl -X POST "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot/create" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Cloudless Assistant",
    "personality": "professional_friendly",
    "language": "auto"
  }'
```

---

## Personality Presets

Five built-in personality presets are available. Each generates a
different system prompt with the `{bot_name}` and `{page_name}` placeholders
filled in:

| Preset | ID | Tone |
|--------|----|------|
| **Professional Friendly** | `professional_friendly` | Balanced, brand-aware, professional yet warm |
| **Casual** | `casual` | Fun, approachable, uses emojis sparingly |
| **Formal** | `formal` | Precise, complete sentences, no slang |
| **Support** | `support` | Patient, helpful, escalates to human when needed |
| **Sales** | `sales` | Enthusiastic, qualifying questions, not pushy |

To see the full description of each preset:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot/personalities"
```

You can also provide a **custom system prompt** when creating a bot, which
overrides the personality preset entirely.

---

## Configuration Reference

All bot configuration is stored in `social_accounts.meta_data.messenger_bot`
and can be edited via the Bot Builder UI or the `PUT /bot` API endpoint.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | "Cloudless Assistant" | Bot display name |
| `enabled` | bool | false | Whether the bot is active |
| `system_prompt` | string | (from personality) | AI system prompt |
| `model` | string | `ai/qwen3:8b-q4_K_M` | AI model (DMR or Cloudflare) |
| `fallback_text` | string | "Thanks for your message..." | Used when AI is unavailable |
| `max_tokens` | int | 250 | Max tokens for AI reply |
| `temperature` | float | 0.6 | 0=deterministic, 1=creative |
| `cooldown_seconds` | int | 300 | Min seconds between replies per conversation |
| `business_hours_start` | string\|null | null | UTC time "07:00" — bot only replies after |
| `business_hours_end` | string\|null | null | UTC time "22:00" — bot only replies before |
| `paused_threads` | list[string] | [] | Thread IDs to skip (human handoff) |
| `reply_to_spam` | bool | false | Whether to auto-reply to spam |
| `reply_to_greetings` | bool | true | Whether to auto-reply to greetings |
| `quick_replies` | list[dict] | 3 defaults | Suggestion buttons shown in inbox |

### Available AI Models

| Model | Provider | Notes |
|-------|----------|-------|
| `ai/qwen3:8b-q4_K_M` | DMR (local) | **Recommended** — best quality, free, private |
| `ai/llama3.2:latest` | DMR (local) | Smaller, faster |
| `ai/gemma3:latest` | DMR (local) | Google model |
| `ai/phi4:latest` | DMR (local) | Microsoft model |
| `ai/qwen2.5:latest` | DMR (local) | Older Qwen version |
| `@cf/meta/llama-3.1-8b-instruct` | Cloudflare | Cloud fallback |

### Language Enforcement

| Value | Behavior |
|-------|----------|
| `auto` | Match the incoming message language (default) |
| `en` | Force English replies |
| `el` | Force Greek replies |
| (any ISO code) | Force that language |

When a non-`auto` language is selected, the bot appends a language
constraint to the system prompt: "You must always reply in {language},
regardless of the incoming message language."

### Business Hours

If both `business_hours_start` and `business_hours_end` are set (in UTC
"HH:MM" format), the bot only replies during those hours. Outside business
hours, messages are skipped (no reply sent). If either is `null`, the bot
replies 24/7.

---

## Activating & Deactivating

### Activate

```bash
curl -X POST "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot/activate" \
  -H "Authorization: Bearer $TOKEN"
```

Response: `{"status": "ok", "enabled": true}`

When activated, the bot will:
- **Personal accounts**: Poll every 120s and reply to unread conversations
- **Business Pages**: Reply to incoming webhook events in real-time

### Deactivate

```bash
curl -X POST "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot/deactivate" \
  -H "Authorization: Bearer $TOKEN"
```

Response: `{"status": "ok", "enabled": false}`

When deactivated, the bot stops all auto-replies. You can still receive
and read messages normally — only the automated replies are paused.

---

## Human Handoff (Pause/Resume)

You can pause the bot for a specific conversation (thread) without
deactivating it globally. This is useful when you want to take over a
conversation personally.

### Pause a thread

```bash
curl -X POST \
  "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot/pause-thread/$THREAD_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Response: `{"status": "ok", "thread_id": "...", "paused": true}`

The bot will skip this thread on all future polls until you resume it.

### Resume a thread

```bash
curl -X POST \
  "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot/resume-thread/$THREAD_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Response: `{"status": "ok", "thread_id": "...", "paused": false}`

### Via the UI

Paused threads appear in the Bot Builder card under "Paused Threads
(Human Handoff)" with a **Resume** button next to each thread ID.

---

## How the Bot Works

### Personal Account Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  Celery Beat (every 120s)                                        │
│  └─▶ poll_personal_messenger task                                │
│      │                                                           │
│      ├─ 1. Check browser session (auto-recover if needed)        │
│      ├─ 2. Fetch conversations from facebook.com/messages         │
│      ├─ 3. Filter: only UNREAD conversations with thread_id       │
│      │                                                           │
│      └─ For each unread conversation:                            │
│         ├─ 4. Read recent messages                               │
│         ├─ 5. Find last inbound message                          │
│         ├─ 6. Skip if already replied (seen-state check)          │
│         ├─ 7. Skip if cooldown active (Redis DB 1, 5 min)        │
│         ├─ 8. Skip if thread paused (human handoff)              │
│         ├─ 9. Detect intent (DMR: business/personal/spam/etc.)   │
│         ├─ 10. Retrieve brand context (ChromaDB RAG)             │
│         ├─ 11. Generate reply (DMR first → CF fallback → static) │
│         ├─ 12. Typing indicator (1.5–5s, scales with reply)      │
│         ├─ 13. Send reply via browser bridge                     │
│         ├─ 14. Store in ChromaDB memory                         │
│         └─ 15. Mark seen + set cooldown (Redis DB 1)             │
└─────────────────────────────────────────────────────────────────┘
```

### Business Page Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  Meta Webhook (real-time push)                                   │
│  └─▶ Webhook sidecar (port 9230)                                │
│      ├─ Event deduplication                                      │
│      └─▶ social-api /messenger/webhook endpoint                 │
│          │                                                       │
│          └─ For each inbound message:                            │
│             ├─ 1. Check bot enabled                              │
│             ├─ 2. Check cooldown (Redis DB 1)                    │
│             ├─ 3. Check thread pause (human handoff)            │
│             ├─ 4. Detect intent (DMR)                           │
│             ├─ 5. Retrieve brand context (ChromaDB RAG)         │
│             ├─ 6. Generate reply (DMR → CF → fallback)          │
│             ├─ 7. Send via Graph API /me/messages               │
│             ├─ 8. Store in ChromaDB memory                      │
│             └─ 9. Set cooldown (Redis DB 1)                     │
└─────────────────────────────────────────────────────────────────┘
```

### Intent Detection

The bot classifies each incoming message into one of five intents using
DMR (Qwen3 8B) with Cloudflare fallback:

| Intent | Description | Bot behavior |
|--------|-------------|-------------|
| `business` | Business inquiry about services/products | Professional, informative, suggests website/call |
| `personal` | Personal message for the account owner | Warm acknowledgment, says owner will reply |
| `question` | General question | Concise, accurate answer |
| `greeting` | Hello/hi/good morning | Brief warm response, asks how to help |
| `spam` | Promotional, scam, or irrelevant | Ignored (if `reply_to_spam=false`) |

### Conversation Memory

The bot stores the last 10 messages per conversation in ChromaDB. When
generating a reply, it retrieves this memory and includes it in the
context, so the bot can reference previous messages naturally:

```
Recent conversation:
Them: Hi, do you offer cloud consulting?
You: Yes! We specialize in cloud and open-source solutions.
Them: What about Kubernetes?
```

### Brand Knowledge RAG

Brand DNA (voice, pillars, positioning, services) is indexed into
ChromaDB. When a message arrives, the bot retrieves relevant brand
context and includes it in the prompt. This ensures replies are
consistent with your brand voice.

To re-index brand knowledge:

```bash
curl -X POST \
  "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/personal/index-brand" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Mobile App (Android/iOS) Behavior

The bot works correctly alongside the Messenger mobile apps:

```
Someone messages you on Messenger
        │
        ▼
  Message syncs to ALL devices:
  ├── Android/iOS Messenger app ← you see it
  └── Browser bridge (server)  ← bot sees it
        │
        ▼
  Bot polls every 120s (personal) or gets webhook (Page)
        │
        ▼
  Did you already read it on your phone?
        │
        ├── YES → Facebook marks it as "read" →
        │         Browser bridge sees it as NOT unread →
        │         Bot SKIPS it (no duplicate reply)
        │
        └── NO  → Facebook shows it as unread →
                  Bot processes it →
                  Generates AI reply →
                  Sends via browser bridge →
                  Reply syncs to your phone too
```

### Safeguards against conflicts

| Layer | What it does |
|-------|-------------|
| **Unread filter** | Bot only processes conversations Facebook marks as unread |
| **Seen-state tracking** | Won't reply to the same message twice |
| **Cooldown (5 min)** | Won't reply again in the same conversation for 300s |
| **Human handoff** | Pause a thread → bot skips it until you resume |

---

## Best Practices Applied

The bot follows Meta's official Messenger Platform best practices:

### 1. Bot Disclosure (Meta Policy)

Meta requires automated chat experiences to disclose they are automated:
- At the beginning of any conversation
- After a significant lapse of time (24h)
- When chat moves from human to automated

**Implementation:**
- First reply in each thread includes `🤖 Auto-reply:` prefix
- System prompt instructs AI to disclose on first contact
- Disclosure state tracked in Redis DB 1 with 24h TTL
- Fallback text also includes disclosure language

### 2. Be Predictable — Typing Indicator

The bot triggers Facebook's "X is typing..." indicator before sending,
with a delay that scales with reply length (1.5–5 seconds). This makes
the bot feel more natural.

### 3. Be Brief

- Max tokens: 250 (tighter replies)
- Temperature: 0.6 (more consistent)
- System prompt enforces "1-3 sentences max"

### 4. Don't Disguise as Human

- System prompt: "You are an automated assistant, NOT Themis himself"
- Safety rule: "Never claim to be Themis or a human"
- Disclosure prefix on first contact

### 5. Fail Gracefully

- Off-topic: reiterates what it can help with
- Fallback text includes human handoff option
- Intent guidance for each category

### 6. Preserve Your Voice

- Bot identity tied to your brand (not a separate entity)
- Uses familiar terms and brand language
- Responds in the user's language naturally

### 7. Safety Guardrails

- Never invents prices, timelines, commitments, or facts
- Never shares personal information about the account owner
- If unsure, offers to connect with the owner personally
- Max 1 emoji per message, only if natural

---

## API Reference

All endpoints are under `/api/v1/messenger` and require a Bearer token.

### Bot Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{account_id}/bot/create` | Create a new bot |
| `GET` | `/{account_id}/bot` | Get bot status and config |
| `PUT` | `/{account_id}/bot` | Update bot config |
| `POST` | `/{account_id}/bot/activate` | Activate bot |
| `POST` | `/{account_id}/bot/deactivate` | Deactivate bot |
| `POST` | `/{account_id}/bot/pause-thread/{thread_id}` | Pause bot for a thread |
| `POST` | `/{account_id}/bot/resume-thread/{thread_id}` | Resume bot for a thread |
| `GET` | `/{account_id}/bot/personalities` | List personality presets |

### Personal Messenger

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{account_id}/personal/conversations` | List conversations |
| `GET` | `/{account_id}/personal/conversations/{thread_id}` | Get messages |
| `POST` | `/{account_id}/personal/send` | Send a message |
| `GET` | `/{account_id}/personal/auto-reply` | Get auto-reply config |
| `PUT` | `/{account_id}/personal/auto-reply` | Update auto-reply config |
| `POST` | `/{account_id}/personal/pause/{thread_id}` | Pause a thread |
| `POST` | `/{account_id}/personal/resume/{thread_id}` | Resume a thread |
| `POST` | `/{account_id}/personal/index-brand` | Index brand knowledge |

### Page Messenger

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{account_id}/setup` | Set up Page Messenger (webhooks, profile) |
| `GET` | `/{account_id}/conversations` | List conversations |
| `GET` | `/{account_id}/conversations/{conversation_id}` | Get messages |
| `POST` | `/{account_id}/send` | Send a message |
| `GET` | `/{account_id}/auto-reply` | Get auto-reply config |
| `PUT` | `/{account_id}/auto-reply` | Update auto-reply config |

### Create Bot Payload

```json
{
  "name": "Cloudless Assistant",
  "personality": "professional_friendly",
  "language": "auto",
  "business_hours_start": "07:00",
  "business_hours_end": "22:00",
  "custom_prompt": "Optional custom system prompt..."
}
```

### Update Bot Payload

All fields are optional — only provided fields are updated:

```json
{
  "name": "New Name",
  "enabled": true,
  "system_prompt": "Updated prompt...",
  "model": "ai/qwen3:8b-q4_K_M",
  "fallback_text": "Fallback message",
  "max_tokens": 250,
  "temperature": 0.6,
  "cooldown_seconds": 300,
  "business_hours_start": "07:00",
  "business_hours_end": "22:00",
  "paused_threads": ["thread_id_1"],
  "reply_to_spam": false,
  "reply_to_greetings": true,
  "quick_replies": [
    {"title": "Services", "payload": "BOT_SERVICES"}
  ]
}
```

---

## Troubleshooting

### Bot not replying (personal account)

1. **Check browser session**: The noVNC browser session must be logged in.
   - Open `https://social.cloudless.gr` → Messenger → check sidecar status
   - If session expired, log in via the noVNC URL

2. **Check worker**: The `social-worker-messenger` container must be running.
   ```bash
   docker compose ps social-worker-messenger
   docker compose logs --tail 20 social-worker-messenger
   ```

3. **Check bot is enabled**:
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot"
   # Look for "enabled": true
   ```

4. **Check cooldown**: The bot waits 300s between replies per conversation.
   If you just replied, it won't reply again for 5 minutes.

5. **Check unread filter**: The bot only replies to conversations Facebook
   marks as unread. If you read the message on your phone, the bot skips it.

### Bot not replying (business Page)

1. **Check Page subscription**: The Page must be subscribed to webhooks.
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/setup"
   ```

2. **Check webhook sidecar**:
   ```bash
   curl http://localhost:9230/health
   ```

3. **Check Page access token**: Token must have `pages_messaging` scope.

### Bot replies are low quality

1. **Check DMR is running**:
   ```bash
   docker model status
   curl http://localhost:12434/engines/v1/models
   ```

2. **Try a different model**: Update `model` in bot config.

3. **Adjust temperature**: Lower (0.3) for more consistent, higher (0.8)
   for more creative.

4. **Re-index brand knowledge**:
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/personal/index-brand"
   ```

### Bot replied when I was already handling it on my phone

This can happen if:
- You read the message but Facebook didn't sync the read state to the
  web UI before the bot's 120s poll fired
- The conversation was still marked as unread in the browser bridge

**Solution**: Pause the thread for human handoff, or increase the
`cooldown_seconds` to give yourself more time.

### Browser session expired

Personal Messenger requires an active browser session. If the session
expires:

1. Open the noVNC URL (shown in Messenger dashboard)
2. Log in to Facebook with your credentials
3. The bot will automatically resume on the next poll

### Monitoring

- **Flower** (Celery dashboard): `http://localhost:5555`
  - Login with env-manager credentials
  - Monitor the `messenger` queue for task status

- **Worker logs**:
  ```bash
  docker compose logs -f social-worker-messenger
  ```

- **Bot status**:
  ```bash
  curl -H "Authorization: Bearer $TOKEN" \
    "http://localhost:8083/api/v1/messenger/$ACCOUNT_ID/bot" | python3 -m json.tool
  ```
