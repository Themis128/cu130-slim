---
name: instagram-dm
description: >-
  Send and receive Instagram direct messages through the Instagram Messaging
  API (same Messenger Platform API as Facebook Pages). Covers send_dm,
  send_dm_template, get_conversations, get_dm_messages, mark_dm_read, and
  send_typing_indicator on InstagramAPIClient. Use when posting to Instagram
  DMs, reading Instagram conversations, building Instagram DM bots, or
  integrating Instagram messaging into the unified inbox.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# Instagram DM (Messaging API)

Send and receive Instagram direct messages through the official Instagram
Messaging API. This uses the same Messenger Platform API as Facebook Page
Messenger, accessed via the Instagram Graph API.

## When to use

- Send a direct message to an Instagram user
- Read Instagram DM conversations
- Build an Instagram DM bot or auto-reply
- Mark Instagram conversations as read
- Show typing indicator in Instagram DMs
- Send template messages outside the 24-hour window
- Integrate Instagram DMs into the unified inbox

## Prerequisites

- Instagram Business or Creator account
- Facebook Page connected to the Instagram account (FB-Login path only)
- Meta app with `instagram_manage_messages` (FB Login) or
  `instagram_business_manage_messages` (Instagram Business Login)
- Valid access token with the messaging scope
- The recipient must have messaged the business first (24-hour window)

## Two token flavors — IMPORTANT

There are two mutually incompatible Instagram token types. Using a token
against the wrong host fails with `OAuthException 190 "Cannot parse access
token"` — that error means wrong host, NOT an invalid token.

|                                | FB Login (linked Page)                                    | Instagram Business Login                                            |
| ------------------------------ | --------------------------------------------------------- | ------------------------------------------------------------------- |
| Token scopes                   | `instagram_basic`, `instagram_manage_messages`, `pages_*` | `instagram_business_basic`, `instagram_business_manage_messages`, … |
| API host                       | `graph.facebook.com`                                      | `graph.instagram.com`                                               |
| `account.meta_data.login_type` | (absent)                                                  | `"business_login"`                                                  |
| Client flag                    | `use_business_login_api=False` (default)                  | `use_business_login_api=True`                                       |

Always construct the client from account metadata:

```python
client = InstagramAPIClient(
    access_token=decrypt_token(account.access_token_enc),
    ig_user_id=account.account_id,
    use_business_login_api=(account.meta_data or {}).get("login_type") == "business_login",
)
```

## IGSID vs app-scoped ID — IMPORTANT

On `graph.instagram.com`, `account.account_id` is the **app-scoped** ID
(e.g. `28747382798219804`) but conversation `participants` and message
`from` carry the **IGSID** (e.g. `17841436821573754`). Resolve ours once
per poll:

```python
me = await client.get_me()          # GET /me?fields=id,user_id,username
my_igsid = me["user_id"]            # IGSID — compare sender/participant ids to this
```

- `recipient.id` for `send_dm` / `send_typing_indicator` / `mark_dm_read`
  must be the **other participant's IGSID** — filter `participants` by
  `id not in {my_igsid, account.account_id}`. Blindly taking
  `participants[0]` is a bug: it is often ourselves, and sending to our own
  ID fails with `IGApiException 100 / subcode 2534014` ("cannot find user").
- `GET /me` without `fields` returns only `{id}` (app-scoped) — always pass
  `fields=user_id` (via `client.get_me()`).

## API methods

All methods are on `InstagramAPIClient` in
`social-automation/backend/app/services/instagram_api.py`.

### send_dm(recipient_id, message)

Send a direct message to an Instagram user. The recipient must have messaged
the business within the last 24 hours.

```python
client = InstagramAPIClient(access_token=token, ig_user_id=ig_id)
result = await client.send_dm(
    recipient_id="17895678901234567",
    message="Thanks for reaching out! How can we help?",
)
```

### send_dm_template(recipient_id, template_name, language, components)

Send a pre-approved template message. Templates must be pre-approved by Meta.
Used for messages outside the 24-hour window with message tags.

```python
result = await client.send_dm_template(
    recipient_id="17895678901234567",
    template_name="welcome_message",
    language={"code": "en"},
    components=[...],
)
```

### get_conversations(limit=25)

List recent Instagram DM conversations with the most recent message preview.

```python
result = await client.get_conversations(limit=25)
# Returns: { "data": [ { "id": "...", "participants": {...}, "messages": {...} } ] }
```

### get_dm_messages(conversation_id, limit=20)

Read messages from a specific Instagram DM conversation.

```python
result = await client.get_dm_messages(conversation_id="123456789", limit=20)
# Returns: { "data": [ { "id": "...", "message": "...", "from": {...} } ] }
```

### mark_dm_read(conversation_id, recipient_id="")

Mark an Instagram DM conversation as read.

```python
await client.mark_dm_read(conversation_id="...", recipient_id="<IGSID>")
```

`graph.instagram.com` rejects `recipient.thread_key` — always pass
`recipient_id` (the peer's IGSID) so the client sends `recipient.id`.

### send_typing_indicator(recipient_id)

Show typing indicator in an Instagram DM conversation.

```python
await client.send_typing_indicator(recipient_id="17895678901234567")
```

## API base URL

```
# FB Login (Page-linked) tokens:
https://graph.facebook.com/v26.0/{ig_user_id}/messages
# Instagram Business Login tokens:
https://graph.instagram.com/v26.0/{ig_user_id}/messages   # /me/* also works
```

## Required permission scopes

```
# FB Login:        instagram_basic instagram_manage_messages pages_show_list …
# Business Login:  instagram_business_basic instagram_business_manage_messages …
```

## Live verification

```bash
# All ops, read-only safe (typing + mark_read are no-ops for the user):
docker compose exec -T social-api python - <<'EOF'
import asyncio
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
from app.services.instagram_api import InstagramAPIClient

async def main():
    async with async_session_maker() as db:
        a = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform == "instagram"))).scalars().first()
        meta = a.meta_data or {}
        c = InstagramAPIClient(
            access_token=decrypt_token(a.access_token_enc),
            ig_user_id=a.account_id,
            use_business_login_api=meta.get("login_type") == "business_login",
        )
        print("me:", await c.get_me())
        convos = await c.get_conversations(limit=3)
        print("convos:", len(convos.get("data", [])))
asyncio.run(main())
EOF
```

## 24-hour messaging window

The Instagram Messaging API follows the same 24-hour window as Facebook
Page Messenger:

- **Within 24 hours**: Send any message with `messaging_type: RESPONSE`
- **Outside 24 hours**: Must use `messaging_type: MESSAGE_TAG` with an
  approved tag (`ACCOUNT_UPDATE`, `HUMAN_AGENT`, `CONFIRMED_EVENT_UPDATE`)
- **Templates**: Pre-approved by Meta, sent with `MESSAGE_TAG`

## Integration with unified inbox

Instagram DMs are automatically included in the unified inbox:

```bash
curl -s http://localhost:8083/api/v1/inbox/inbox \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(syss.stdin); [print(c) for c in d['conversations'] if c['platform']=='instagram']"
```

## Source files

- `social-automation/backend/app/services/instagram_api.py` — InstagramAPIClient with DM methods
- `social-automation/backend/app/api/inbox.py` — Unified inbox integration

## Future enhancements

- Instagram DM webhook handling (receive messages in real-time)
- Instagram DM auto-reply with AI (like Page Messenger auto-reply)
- Instagram DM template management API
- Instagram DM analytics (response time, volume)
