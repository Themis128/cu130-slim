# Nextcloud Talk ↔ business messenger bridge

Business DMs from Facebook Page Messenger, personal Messenger, Instagram,
and LinkedIn surface as native Nextcloud Talk rooms — readable and
replyable from the Talk Android app and the Windows Talk desktop client
with no client plugins.

## Design

One **group Talk room per external conversation**, named
`<Platform> · <Contact>` (`FB · Jane Doe`, `IG · @user`, `LI · Jane Doe`)
so rooms read like any other chat in the Talk sidebar.

| Direction | Path |
|---|---|
| Inbound | Celery beat `talk-bridge-sync` (every `TALK_BRIDGE_POLL_S`, default 180s) → `sync_rooms()` maps new conversations to Talk rooms via OCS `POST /room` → `inbound_sync()` posts unseen platform messages via the Talk bot endpoint (`bot_reply`, HMAC-signed) |
| Outbound | Talk bot webhook `POST /api/v1/nextcloud/talk-bot` (existing, HMAC-verified) → `_bridge_reply()` → `outbound()` routes the text through the platform send lane; errors are posted back into the room |

The room map is the `talk_rooms` table — the whitelist for bot replies
(rooms not in the table get no response; SSRF-safe like the configured
ops rooms).

## Send lanes

| Room platform | Lane |
|---|---|
| `messenger` (FB Page) | `MessengerAPIClient.send_text` (Graph Send API, page token) |
| `personal_messenger` | `BrowserBridgeClient.send_personal_messenger_message` (browser bridge; checks session first) |
| `instagram` | `InstagramAPIClient.send_dm` (Instagram Messaging API; recipient ig-user-id captured at sync) |
| `linkedin` | `LinkedInSidecar.send_message` (conversation API) |

## Personal-Messenger scrape hygiene

The personal-Messenger lane scrapes rendered DOM text, so call logs,
E2EE/restore banners, sender labels, timestamps and buttons can arrive
as fake "messages". `_pm_clean_text` keeps a single-line blob unless it
is provably chrome (call row or system banner), and in multi-line blobs
strips chrome only from the leading/trailing edges — interior lines are
verbatim. Call rows collapse to a `📞 <call>` marker; a leading
`You sent` line or a scraper `sender: "me"` marks the message as ours.
Mirrored posts are sent `silent` (no Talk notifications). If the stored
`last_inbound_key` is missing from the feed (filtered row / changed
hash), the room re-anchors to the newest message without reposting.

## Room commands (typed in the room)

- `mute` — replies stay in Talk, nothing goes to the contact
- `unmute` — resume bridging
- `help` — one-line reminder

Successful sends are silent (the user's own message is already in the
room); only failures post back, e.g. `⚠️ Send failed: 24h window
expired`.

## Config

- `TALK_BRIDGE_ENABLED` — master switch (requires `NEXTCLOUD_TALK_NOTIFY` + `NEXTCLOUD_TALK_BOT_SECRET`)
- `TALK_BRIDGE_POLL_S` — inbound cadence (default 180)
- The Talk bot must be installed and added to the mapped rooms it should
  route (`occ talk:bot:setup <bot_id> <room_token>`); bridge posts fall
  back to the app-password chat API when the bot isn't in a room.

## API

`GET /api/v1/talk-bridge/status` · `GET /talk-bridge/rooms` ·
`POST /talk-bridge/sync` · `POST /talk-bridge/rooms/{token}/send` ·
`POST /talk-bridge/rooms/{token}/toggle`

## Limits

- WhatsApp is read-only for now (24h template-window rules make blind
  text sends fail); excluded from room sync.
- Instagram sends only work within the 24h response window after the
  contact last messaged — same rule as the native API.
- Attachments arrive as `📎 <link>` lines.
