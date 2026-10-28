---
name: viber-ops
description: Viber Business bot integration in SocialAuto — the official Viber REST Bot API client (chatapi.viber.com), account connect/credentials, webhook with HMAC signature verification, send/broadcast, and the DMR-backed auto-reply bot. Use for Viber bot setup, sends, webhook debugging, or subscriber questions.
---

# Viber Ops

SocialAuto supports **Viber bots** via the official REST API
(`https://chatapi.viber.com/pa/*`, `X-Viber-Auth-Token` header). Repo adopted
as reference: `Viber/viber-bot-python` — the official Python SDK. We did NOT
depend on it: it is unmaintained (2016-era, sync `requests`, vendored message
classes). `app/services/viber_api.py` is a thin async client over the same
documented REST surface — same pattern as `telegram_api.py`.

## Backend surface

- `app/services/viber_api.py` — `ViberAPIClient` (async httpx):
  `get_account_info`, `set_webhook`, `unset_webhook`, `send_text`,
  `send_picture`, `send_video`, `send_file`, `send_url`, `send_message`,
  `broadcast_text`/`broadcast_message` (max 300 receivers), `get_online`,
  `get_user_details`; plus `verify_signature` (HMAC-SHA256 body signature)
  and `parse_webhook_event`. `VIBER_STATUS_CODES` maps the JSON `status`
  field (HTTP stays 200 on app errors — status 0 = ok).
- `app/api/viber.py` — router at `/api/v1/viber`:
  `POST /connect`, `PUT /{id}/credentials`, `POST /{id}/setup-webhook`,
  `POST /{id}/delete-webhook`, `GET /{id}/setup-status`,
  `POST /{id}/send`, `POST /{id}/send-picture`, `POST /{id}/broadcast`,
  `GET|PUT /{id}/auto-reply`, `POST /{id}/threads/{uid}/pause|resume`,
  `POST /viber/webhook/{account_id}` (signature-verified).
- `app/services/viber_chatbot.py` — inbound pipeline reusing
  `whatsapp_chatbot`/`messenger_chatbot` helpers (intent detect, brand RAG,
  DMR-first reply generation) with `viber:` Redis keys.
- `scripts/viber_tool.py` — ops CLI (see below).
- `publish_to_platform` skips `viber` like whatsapp/telegram — it is a
  messaging channel, not a feed platform.

## Connecting a bot

New Viber bots require approval via verified partners
(partners.viber.com). Once you have the auth token:

```bash
curl -X POST http://localhost:8083/api/v1/viber/connect \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"auth_token": "<viber-bot-token>", "set_webhook": true}'
```

Token is encrypted into `social_accounts` (`platform="viber"`,
`meta_data.viber_auth_token_enc`). Webhook URL =
`https://social.cloudless.gr/api/v1/viber/webhook/{account_id}` — requires
the public tunnel up.

## Tool: `social-automation/backend/scripts/viber_tool.py`

```bash
docker cp social-automation/backend/scripts/viber_tool.py social-api:/app/scripts/
docker exec social-api python3 /app/scripts/viber_tool.py accounts
docker exec social-api python3 /app/scripts/viber_tool.py info      [-a <uuid>]
docker exec social-api python3 /app/scripts/viber_tool.py webhook   [-a <uuid>]
docker exec social-api python3 /app/scripts/viber_tool.py online u1,u2
docker exec social-api python3 /app/scripts/viber_tool.py user <viber_user_id>
docker exec social-api python3 /app/scripts/viber_tool.py verify-sig
```

Reads tokens from the DB (decrypts server-side); never prints them.
`/app/scripts` is not bind-mounted — `docker cp` first if stale.

## Gotchas

- **HTTP 200 on errors** — always check JSON `status` (see
  `VIBER_STATUS_CODES`); `send_message` to an unsubscribed user → status 6.
- **Broadcast needs Viber approval** — status 15 until granted by a Viber
  account manager. `broadcast_list` cap is 300.
- **Sender name ≤ 28 chars** — client truncates automatically.
- **All media must be public HTTPS URLs** — Viber fetches the URL server-side;
  point it at `social.cloudless.gr/media/...` assets.
- **Webhook signature** — `X-Viber-Content-Signature` =
  hex HMAC-SHA256(auth_token, raw_body); verify before parsing.
- `conversation_started` fires once when a user first opens the bot —
  optionally sends `auto_reply.welcome_message` (only to non-subscribed users;
  replying there subscribes them implicitly).
- `set_webhook` requires HTTPS; empty `url` unsets it.
