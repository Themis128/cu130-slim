---
name: telegram-channel-ops
description: >-
  Telegram channel administration through SocialAuto — the `telegram_*` MCP
  tools on the `socialauto` server (bot-side ops via Cloudless_newbot) plus
  the optional `telegram` MCP server (chigwell/telegram-mcp, Telethon user
  session) for the one thing bots cannot do: create channels. Covers channel
  discovery via webhook my_chat_member events, named invite links for
  join-source attribution, descriptions, pinning, and the private-channel /
  Telegram Stars subscription path.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Telegram Channel Ops

Read `social-content-core` and `messaging-channel-ops` first.

## Hard platform rules

- **Bots can never create channels or groups.** Only real user accounts can.
  No Bot API method, no BotFather command. The channel must be created either
  by the user in the Telegram app (2 min) or by a Telethon **user session**
  (the `telegram` MCP server) acting as the owner.
- **Bot needs admin rights** in the channel for every admin op below:
  Post messages, Edit messages, Invite via link, Pin messages. The owner
  remains the only human admin — the bot is a scoped service admin.
- **Private channel is required for Telegram Stars paid subscriptions**
  (paid invite links only work on private chats). Private channels have no
  public @username — members join via invite links only.
- **`getUpdates` does not work while a webhook is set** — channel discovery
  comes from `my_chat_member` webhook events, persisted to
  `social_accounts.meta_data.telegram_channels` (keyed by chat_id).

## Tool surfaces

### 1. `socialauto` MCP — `telegram_*` tools (preferred, uses stored bot token)

Account: `f75d0132-0964-4236-9766-a6603c997dfd` (Cloudless_newbot).
`account_id` arg is optional on every tool — auto-resolves to the active
telegram account.

| Tool | Endpoint | Use |
|------|----------|-----|
| `telegram_list_channels` | GET `/telegram/{acc}/channels` | Discover chat_id after bot is added as admin |
| `telegram_chat_info` | GET `/telegram/{acc}/chat-info` | Title/desc + member count + `bot_is_admin` |
| `telegram_chat_admins` | GET `/telegram/{acc}/chat-admins` | Verify owner + bot admin rights |
| `telegram_member_count` | GET `/telegram/{acc}/chat-members` | Growth tracking |
| `telegram_set_chat_description` | PUT `/telegram/{acc}/chat-description` | Channel description (≤255 chars) |
| `telegram_create_invite_link` | POST `/telegram/{acc}/invite-link` | Named link (`ig`/`threads`/`website`) or primary if no options |
| `telegram_send_message` | POST `/telegram/{acc}/send` | Welcome/posts |
| `telegram_pin_message` | POST `/telegram/{acc}/pin` | Pin the welcome post |

### 2. `telegram` MCP — user session (optional, for channel creation)

- Install: `~/tools/telegram-mcp` (chigwell/telegram-mcp, Telethon).
  Registered in `.devin/mcp_config.json`; runs `uv run telegram-mcp`.
- **Credentials needed** (in `~/tools/telegram-mcp/.env`, never committed):
  `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` from https://my.telegram.org/apps,
  then `uv run telegram-mcp-generate-session --qr` for the session.
- **Never `uvx telegram-mcp`/`pip install telegram-mcp`** — that PyPI name is
  a different package; credentials sent to it leak. Always run from the clone.
- Once authed it can `create_channel`, set it private, add the bot as admin —
  zero manual UI steps. Everything else should still go through the bot-side
  tools (keeps the bot-token surface canonical for SocialAuto).

## SocialAuto UI + notifications

- **Dashboard page**: `/telegram` → *Channel management* card — discovered
  channels dropdown, "Manage this channel" (stores `meta.telegram_channel`),
  member count + `bot_is_admin`, description editor, named invite-link
  creator, post composer with pin-after-send, notification status.
- **Endpoints**: `GET/PUT /telegram/{acc}/channel-config` (managed channel),
  `GET /telegram/{acc}/notify-config` (slack/email routing status).
- **Notifications** (`app/services/telegram_notify.py`): webhook events fan
  out to Slack **#socialauto-telegram** (`C0C8DU44CAV`) + email
  `TELEGRAM_NOTIFY_EMAIL` (fallback `DIGEST_EMAIL_TO`). Events: member
  joined/left, join request, bot added/removed/admin. Joins carry
  `invite_link_name` for source attribution (needs `chat_member` in
  `TELEGRAM_ALLOWED_UPDATES` — re-run webhook setup if the webhook was
  registered before it was added).
- Env: `SLACK_TELEGRAM_WEBHOOK_URL` / `SLACK_TELEGRAM_CHANNEL_ID` /
  `TELEGRAM_NOTIFY_EMAIL` — compose `x-worker-env` + `social-api` pass-through.

## New-channel setup sequence

1. Channel exists (user-created or via `telegram` MCP) + bot added as admin.
2. `telegram_list_channels` → get `chat_id` (negative, `-100…` for channels).
3. `telegram_chat_info` → verify `bot_is_admin`.
4. `telegram_set_chat_description` → branded description.
5. `telegram_create_invite_link` with `name` per source: `ig`, `threads`,
   `website` — attribution is the name shown in admin surfaces.
6. `telegram_send_message` → welcome post, then `telegram_pin_message`.
7. `telegram_member_count` → baseline for growth tracking.

## Funnel + safety

- Promote only via **Instagram + Threads** initially — each gets its own named
  link so join sources are attributable. Website link (`name=website`) is added
  to `cloudless.gr` `SOCIAL_ACCOUNTS` + `/links` once generated.
- **Invite links grant access — treat as secrets** in logs/output.
- Later: `createChatSubscriptionInviteLink` (Bot API, Stars payments) gates
  the channel for paid subs — private channel from day one means no rebuild.
- InviteLinkRequest: `member_limit` (1-99999), `expire_date` (unix ts),
  `creates_join_request` (approval flow) — mutually exclusive with member_limit.
