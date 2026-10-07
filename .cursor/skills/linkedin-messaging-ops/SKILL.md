---
name: linkedin-messaging-ops
description: LinkedIn personal-account DMs and InMail replies through SocialAuto — the browser-sidecar DM endpoints, in-thread reply mechanics, the 429 circuit-breaker behavior, the deferred-reply n8n workflow, and the session recovery ladder. Use when reading LinkedIn conversations, replying to InMail/messages, or debugging "circuit open" / "redirect loop" / "not logged in" DM failures.
---

# LinkedIn Messaging / InMail Ops

LinkedIn has **no DM API** for personal or org accounts. All messaging goes
through the `linkedin-browser-sidecar` (port 9225) Playwright session, exposed
by SocialAuto as REST endpoints. Replies inside an existing thread (DM or
InMail) are always **free** — no InMail credits needed on our side, and the
sender even gets their credit refunded when we reply. Always reply **in the
existing thread**, never via profile-based "Message" compose (creates a
separate DM and may not exist for non-connections).

## Accounts

| Account | UUID | Use |
|---|---|---|
| Personal (Themistoklis) | `18d5cd59-f0c2-4fc4-986e-03601734c7a5` | InMail/DM replies, recruiter threads |
| Company Page `cloudless-gr` | `9c4451bb-e820-489f-8676-76ddbc788ffe` | Org inbox (same endpoints) |

## SocialAuto DM endpoints

Base: `SOCIAL_API_URL` (default `http://127.0.0.1:8083`), Bearer token from
`POST /api/v1/auth/login` (form `username`/`password`, admin needs `otp` from
`SOCIAL_TOTP_SECRET` — see the TOTP code node in `n8n-workflows/`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/linkedin/{account_id}/dm/conversations` | List conversations → `{conversations:[{name,preview,thread_id,thread_url,unread,time}]}` |
| GET | `/api/v1/linkedin/{account_id}/dm/threads/{thread_id}` | Read thread → `{messages:[{sender,text,time}]}` |
| POST | `/api/v1/linkedin/{account_id}/dm/threads/{thread_id}/send` | In-thread reply `{text}` |

Sidecar direct equivalents: `GET /messages`, `GET /messages/{tid}`,
`POST /messages/{tid}/send` on `http://127.0.0.1:9225`.

Each call navigates a real browser page — **list + read + send each count as a
LinkedIn navigation**. Batch reads; don't loop over dozens of threads.

## The 429 circuit breaker (critical)

The sidecar persists a circuit at `/data/rate-limit.json` (`until`, `reason`,
`trippedAt`). Any navigation that gets HTTP 429 **or a redirect loop** trips it
for `LINKEDIN_RATE_LIMIT_COOLDOWN_MS` (default **6h**). While open, every
navigate returns:

```json
{"error":"LinkedIn circuit open until ... (http_429); skipped navigate ...","code":"RATE_LIMITED"}
```

**Every probe past the `until` timestamp that still hits LinkedIn-side
throttling RE-TRIPS the circuit for another 6h.** Checking `/session` or
listing conversations to "see if it works now" actively extends the block.
Treat the circuit like a rate-limit `Retry-After`: don't probe early.

- Inspect without touching LinkedIn: `docker exec linkedin-browser-sidecar cat /data/rate-limit.json` and `GET /health` (reports `has_session`, `rate_limited`, `rate_limit_until` — no navigation).
- `GET /session` DOES navigate `/feed/` — it will trip/extend the circuit if the block is live.
- "redirect loop (likely rate-limited)" means LinkedIn is still throttling the session/IP **or** the session expired and bounces to the authwall — the two are indistinguishable while the circuit is open.
- `POST /session/clear-rate-limit` exists but only clear it when the `until` is stale — clearing while LinkedIn still blocks just burns a fresh 6h trip.
- Recovery ladder: stale-circuit clear → `POST /login` (credential login via `LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD` secrets; sends a **2FA app push the owner must approve** — warn before triggering) → noVNC manual login. **Never cookie-transplant LinkedIn — `li_at` is fingerprint-bound and gets revoked globally.**

## Deferred replies — `linkedin-dm-deferred-reply` n8n workflow

For sending a reply that must ride out a live circuit (or any scheduled DM):

```text
POST http://127.0.0.1:5678/webhook/linkedin-dm-deferred-reply
{
  "account_id": "18d5cd59-...",          # optional, defaults to personal
  "execute_after": "2026-10-08T10:00:00Z",  # ISO; converted to Europe/Athens internally
  "match_keywords": ["hikerapi", "xpoz"],   # optional; defaults to IG-data pitch terms
  "reply_text": "Hi, thanks for reaching out. ..."
}
```

Flow: webhook ack → Wait node until `execute_after` → SocialAuto login
(TOTP) → list conversations → score candidates (preview match > unread >
recent, max 5 thread navigations) → read threads → **send only when exactly
one thread's messages match the keywords** (ambiguous/zero match → execution
fails visibly, nothing sent). HTTP nodes retry 3×5 min on transient errors.

Response shape `onReceived` — the webhook acks immediately; delivery status is
the n8n execution (`n8n_list_executions` → `waiting` until the Wait resumes).

## Reply procedure

1. `GET .../dm/conversations` — find the thread. Preview text is ~80 chars of
   the last message; `unread` flags new inbound.
2. `GET .../dm/threads/{thread_id}` — confirm it's the right conversation
   (check `sender`/`text` of last inbound). Do NOT send on name-match alone.
3. `POST .../dm/threads/{tid}/send` with the reply text.
4. If any call returns `RATE_LIMITED`, stop immediately — read
   `/data/rate-limit.json` for `until`, and either wait for that +margin or use
   the deferred workflow with `execute_after` past it (LinkedIn soft blocks
   often run 24h+; schedule past the longer window).

## Also see

- `session-ops` — hourly heal sweep, noVNC/Playwright login bootstrap
- `linkedin-content-ops` — posting (not messaging)
- `linkedin-browser-sidecar/server.js` — `/login` works while the circuit is
  open (LinkedIn login page isn't throttled; the flagged session cookie is)
