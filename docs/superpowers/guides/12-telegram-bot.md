# Telegram Bot channel

Connect a Telegram bot to SocialAuto with the official [Bot API](https://core.telegram.org/bots/api), then send messages and run AI auto-reply from the dashboard.

## Prerequisites

1. A Telegram account.
2. Public HTTPS for webhooks (production: Cloudflare tunnel → `https://social.cloudless.gr`).
3. Admin login to SocialAuto.

## Steps

### 1. Create a bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot` and follow the prompts.
3. Copy the authentication token (`123456:ABC-…`). Store it only in SocialAuto credentials — treat it as a password.

### 2. Connect in SocialAuto

1. Open **Telegram** in the sidebar (`/telegram`), or **Channels** → Telegram → Connect (opens `/telegram`).
2. Paste the BotFather token and enable **Register HTTPS webhook**.
3. Click **Connect bot**.
4. Confirm setup status shows credentials + token valid (`getMe`).

### 3. Webhook

SocialAuto registers:

`https://social.cloudless.gr/api/v1/telegram/webhook/{account_id}`

with a per-account `secret_token` (header `X-Telegram-Bot-Api-Secret-Token`).

- Webhook and `getUpdates` are mutually exclusive — SocialAuto uses webhook only.
- Local-only stacks without public HTTPS can still store credentials and send manually; inbound auto-reply needs the tunnel URL.

Override base URL with env `TELEGRAM_WEBHOOK_BASE` (default `https://social.cloudless.gr/api/v1`).

### 4. Send a test message

Bots **cannot start chats**. The user must open the bot first (`t.me/your_bot`).

1. Message the bot from Telegram.
2. Read `chat_id` from logs / webhook payload, or use a helper like `@userinfobot` for your own id.
3. On `/telegram` → **Send message**, enter `chat_id` and text.

### 5. Auto-reply / bot persona

1. Enable **AI Auto-Reply**, or **Create default bot** then **Activate**.
2. Inbound text updates trigger the same brand-voice + CF Workers AI → DMR pipeline as other messengers.
3. Pause a conversation with `POST /api/v1/telegram/{id}/threads/pause` `{ "chat_id": "…" }` if a human takes over.

## Limits (official)

- Text messages: up to 4096 characters.
- Webhook ports: 443, 80, 88, 8443; URL must be HTTPS.
- Feed-style publishing is not supported — Telegram is messaging-only (soft-skipped like WhatsApp).

## Group watch (stay updated)

Keep yourself updated from a Telegram group (e.g. **Visibility Era 2.0**) using official Bot API webhooks + `sendMessage` / optional `forwardMessage`.

### What it does

| Feature | Behavior |
|---------|----------|
| Buffer | Stores recent group text messages in Redis (~48h) |
| Keyword alerts | DMs you immediately when keywords match |
| Mention alerts | DMs you when someone `@` the bot |
| Daily digest | Summarizes buffered messages to your private chat |
| Membership | Auto-registers groups when the bot is added (`my_chat_member`) |

### Setup

Telegram’s Bot API **cannot** disable Group Privacy or add the bot to a group by itself. SocialAuto prepares official deep links so you only tap:

1. In SocialAuto **Telegram → Group watch → Prepare Telegram links**, or call `POST /api/v1/telegram/{id}/group-watch/setup-links`.
2. Tap **Link my DM** → `https://t.me/<bot>?start=linkowner` (Start in Telegram).
3. Tap **Add to group** → `https://t.me/<bot>?startgroup=watch` → pick **Visibility Era 2.0**.
4. Tap **BotFather privacy** → `/mybots` → bot → **Bot Settings** → **Group Privacy** → **Turn off**.

`setMyCommands` registers `/linkowner` in the Telegram command menu. Webhook `allowed_updates` includes `my_chat_member` so joining the group auto-registers it for watch.

### API

| Action | Endpoint |
|--------|----------|
| Get/set config | `GET/PUT /api/v1/telegram/{id}/group-watch` |
| Add chat | `POST /api/v1/telegram/{id}/group-watch/add-chat` |
| Digest now | `POST /api/v1/telegram/{id}/group-watch/digest-now` |
| Activity | `GET /api/v1/telegram/{id}/group-watch/activity` |

Celery beat task `telegram-group-digests` runs hourly and sends when the local hour matches `digest_hour`.

## DM lead-capture funnel (channel → DM → Lead)

The channel agent (`telegram_agent.py`) turns broadcast posts into qualified leads.

```
offer post (authority/engagement/proof/offer pillars, weekly cadence)
  → offer posts append a "💬 Chat with us" button
  → t.me/<bot>?start=audit deep link
  → "/start audit" in the bot DM → lead-capture FSM (Redis, 1h TTL)
  → name → email → company size (buttons) → interest (buttons)
  → Lead row (source=telegram_dm) → owner notification (Slack/email)
```

### How it works

- The FSM is shared with Messenger/Instagram (`app/services/lead_capture.py`);
  Telegram uses `LeadSource.telegram_dm` and `thread_id = chat_id`.
- `LEAD_SIZE_*` / `LEAD_INTEREST_*` inline buttons return via `callback_query`.
- **Tracked links:** every owned-domain CTA (`cloudless.gr`,
  `social.cloudless.gr`, the checklist PDF) is tagged
  `utm_source=telegram & utm_medium=channel_post & utm_campaign=cloudless-channel-agent
  & utm_content=<offer key>`; `t.me` links are untouched and existing `utm_*`
  params are preserved. `channel_post` analytics events record the campaign.
- **Join attribution:** create named invite links (`create_chat_invite_link`)
  per surface — `website` for cloudless.gr `/telegram`, `social` for
  social.cloudless.gr placements. Join events carry `invite_link_name`.

### Safety gates

- Lead prompts only run when `telegram_auto_reply.enabled` is true (the
  deactivate-bot switch) **and** the thread is not paused for human handoff.
- If the Telegram send of a prompt fails, the Redis session is rolled back —
  the user's next message re-asks the question they never saw.
- Stale inline buttons (Telegram keeps old keyboards live) re-ask the current
  step instead of counting as invalid answers.
- **Owner linking is protected:** only `/linkowner`, `/watch`, or
  `/start linkowner` bind `owner_chat_id`. A bare `/start` binds only when no
  owner is set; `/start <payload>` (e.g. `audit`) always goes to the funnel and
  can never re-route owner alerts to a reader.

## API cheat sheet

| Action | Endpoint |
|--------|----------|
| Connect | `POST /api/v1/telegram/connect` |
| Credentials | `PUT /api/v1/telegram/{id}/credentials` |
| Setup status | `GET /api/v1/telegram/{id}/setup-status` |
| Set webhook | `POST /api/v1/telegram/{id}/setup-webhook` |
| Send | `POST /api/v1/telegram/{id}/send` |
| Group watch | `GET/PUT /api/v1/telegram/{id}/group-watch` |
| Webhook | `POST /api/v1/telegram/webhook/{id}` |

## Out of scope (v1)

Personal Telegram Web / MTProto login, Telegram Business Mode, Mini Apps, payments, and full group admin actions (ban/kick/mute). Group **watch** (alerts + digest) is supported.
