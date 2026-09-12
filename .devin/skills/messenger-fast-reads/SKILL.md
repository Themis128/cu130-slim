---
name: messenger-fast-reads
description: >-
  Fast conversation and message reads for personal Facebook Messenger via
  m.facebook.com mobile basic HTML. Saves 3-4 seconds per read compared to
  the full SPA at facebook.com/messages. Covers get_personal_messenger_conversations_fast
  and get_personal_messenger_messages_fast on BrowserBridgeClient, with
  automatic fallback to the full SPA. Use when optimizing personal Messenger
  polling speed, reducing browser bridge latency, or debugging slow reads.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# Messenger Fast Reads (Mobile Basic HTML)

Read personal Facebook Messenger conversations and messages using the
mobile basic version of Facebook (m.facebook.com), which renders server-side
and loads much faster than the full SPA at facebook.com/messages.

## When to use

- Speed up personal Messenger polling (Celery task runs every 120s)
- Reduce browser bridge latency for conversation reads
- Debug slow personal Messenger reads
- Optimize the poll-personal-messenger task performance
- Fallback when the full SPA fails to load

## Performance comparison

| Method | URL | Load time | Rendering |
|--------|-----|-----------|-----------|
| Full SPA | facebook.com/messages | ~5-7s | Client-side (React hydration) |
| Mobile basic (fast) | m.facebook.com/messages | ~2s | Server-side (basic HTML) |
| Savings | — | **~3-4s per read** | — |

## API methods

Both methods are on `BrowserBridgeClient` in
`social-automation/backend/app/services/browser_bridge.py`.

### get_personal_messenger_conversations_fast()

Read the conversation list via mobile basic HTML. Falls back to the full
SPA method if mobile basic returns no conversations.

```python
bridge = BrowserBridgeClient("http://browser-novnc:9223")
result = await bridge.get_personal_messenger_conversations_fast()
# Returns: { "conversations": [...], "count": N, "source": "mobile_basic" | "full_spa" }
```

### get_personal_messenger_messages_fast(thread_id, is_e2ee=False)

Read messages from a specific thread via mobile basic HTML. Falls back to
the full SPA method if mobile basic returns no messages.

```python
result = await bridge.get_personal_messenger_messages_fast(
    thread_id="1234567890",
    is_e2ee=False,
)
# Returns: { "messages": [...], "count": N, "source": "mobile_basic" | "full_spa" }
```

## Fallback chain

```
Mobile basic (m.facebook.com)
    │ fails or returns empty
    ▼
Full SPA (facebook.com/messages)
```

The `source` field in the response indicates which method was used:
- `"mobile_basic"` — fast path succeeded
- `"full_spa"` — fell back to full SPA (implicit, when fast returns the SPA result)

## Integration with polling task

The fast reads are wired into the `poll-personal-messenger` Celery task
(`social-automation/backend/app/worker/tasks/personal_messenger.py`):

```python
# 1. Fetch conversations (fast mobile-basic first, fallback to full SPA)
convos_result = await bridge.get_personal_messenger_conversations_fast()

# 2. Read recent messages (fast mobile-basic first, fallback to SPA)
msgs_result = await bridge.get_personal_messenger_messages_fast(thread_id, is_e2ee=is_e2ee)
```

## How it works

1. Navigate to `https://m.facebook.com/messages` (or `m.facebook.com/messages/t/{thread_id}/`)
2. Wait 2 seconds for server-side rendering (vs 5s for SPA hydration)
3. Extract conversation/message data from the basic HTML DOM
4. If no data found, fall back to the full SPA method

The mobile basic version uses simpler HTML:
- Anchor tags with `/messages/t/` or `/messages/e2ee/t/` hrefs
- Bold text (`<strong>`, `<b>`) for unread indicators
- Server-rendered message containers (no React hydration needed)

## Source files

- `social-automation/backend/app/services/browser_bridge.py` — Fast read methods
- `social-automation/backend/app/worker/tasks/personal_messenger.py` — Polling task integration

## Browser bridge endpoints used

- `POST /session/navigate` — Navigate to m.facebook.com URL
- `POST /session/evaluate` — Extract conversation/message data from DOM

## Future enhancements

- Cache conversation list for 30s to avoid repeated reads
- Incremental message reads (only fetch new messages since last read)
- Background pre-fetch of conversation list (daemon mode)
- Support for m.basic.facebook.com (even lighter than m.facebook.com)
