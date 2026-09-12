---
name: unified-inbox
description: >-
  Aggregate conversations across all platforms (Facebook Page Messenger,
  personal Messenger, Instagram DMs, WhatsApp) into a single unified inbox
  via the SocialAuto API. Use when viewing all DMs in one place, building a
  cross-platform inbox UI, or checking unread counts across platforms.
  Covers the GET /api/v1/inbox/inbox endpoint and the UnifiedConversation schema.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# Unified Inbox

Aggregate conversations from all connected social platforms into a single
normalized view. The backend foundation for a cross-platform DM inbox UI.

## When to use

- View all DMs/conversations across all platforms in one API call
- Build a unified inbox frontend (Messenger + Instagram + WhatsApp + Threads)
- Check unread message counts per platform
- Monitor conversation volume across platforms
- Debug missing conversations from a specific platform

## API endpoint

```
GET /api/v1/inbox/inbox
Authorization: Bearer <token>
```

Returns all conversations from connected accounts, fetched in parallel.
Failed platform fetches are skipped (non-fatal) so the inbox always returns
available conversations even if one platform is down.

## Response schema

```json
{
  "conversations": [
    {
      "platform": "messenger",          // messenger, personal_messenger, instagram, whatsapp
      "account_id": "uuid",
      "account_name": "cloudless.gr",
      "thread_id": "1234567890",
      "sender_name": "John Doe",
      "preview": "Hi, I have a question...",
      "unread": true,
      "timestamp": "2026-09-12T10:30:00Z",
      "url": "https://www.facebook.com/messages/t/1234567890/",
      "e2ee": false
    }
  ],
  "total": 15,
  "by_platform": {
    "messenger": 5,
    "personal_messenger": 8,
    "instagram": 2
  }
}
```

## Platforms supported

| Platform | Source | Method |
|----------|--------|--------|
| Facebook Page Messenger | Graph API | `FacebookAPIClient.get_conversations()` |
| Facebook Personal Messenger | Browser bridge | `BrowserBridgeClient.get_personal_messenger_conversations_fast()` |
| Instagram DMs | Instagram Messaging API | `InstagramAPIClient.get_conversations()` |
| WhatsApp | Cloud API | Placeholder (requires conversation tracking DB) |

## Architecture

```
GET /api/v1/inbox/inbox
         │
         ▼
    social-api (port 8083)
    app/api/inbox.py
         │
         ├──▶ Page Messenger (Graph API)
         ├──▶ Personal Messenger (browser bridge, fast mobile read)
         ├──▶ Instagram DMs (Instagram Messaging API)
         └──▶ WhatsApp (placeholder)
         │
         ▼
    Parallel asyncio.gather()
    (non-fatal: failed platforms skipped)
         │
         ▼
    Sort by unread first, then sender name
    Return UnifiedInboxResponse
```

## Authentication

```bash
# Login as admin
TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['access_token'])")

# Fetch unified inbox
curl -s http://localhost:8083/api/v1/inbox/inbox \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

## Usage examples

```bash
# Get all conversations
curl -s http://localhost:8083/api/v1/inbox/inbox \
  -H "Authorization: Bearer $TOKEN"

# Filter by platform (client-side)
curl -s http://localhost:8083/api/v1/inbox/inbox \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(c) for c in d['conversations'] if c['platform']=='instagram']"

# Count unread
curl -s http://localhost:8083/api/v1/inbox/inbox \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(1 for c in d['conversations'] if c['unread']))"
```

## Source files

- `social-automation/backend/app/api/inbox.py` — Unified inbox API endpoint
- `social-automation/backend/app/api/__init__.py` — Router registration

## Future enhancements

- WhatsApp conversation tracking (requires DB table for conversation state)
- Threads DM support (browser bridge, when available)
- Real-time updates via WebSocket
- Cross-platform search (search messages across all platforms)
- Conversation threading (group messages by sender across platforms)
- Frontend UI component for the unified inbox
