# Nextcloud Talk ↔ business messenger bridge

Read and answer business DMs (Facebook Page Messenger, personal
Messenger, Instagram, LinkedIn) from inside Nextcloud Talk — the Talk
Android app and the Windows Talk desktop client work with no plugins.
One private Talk room per external conversation, named
`<Platform> · <Contact>`.

## Setup (one time)

1. Set in `.env`: `NEXTCLOUD_TALK_NOTIFY=true`,
   `NEXTCLOUD_USERNAME`, `NEXTCLOUD_APP_PASSWORD`,
   `NEXTCLOUD_DAV_URL` (or `NEXTCLOUD_BASE_URL`),
   `NEXTCLOUD_TALK_BOT_SECRET`, then `TALK_BRIDGE_ENABLED=true`.
   Optional: `TALK_BRIDGE_POLL_S=180`.
2. Restart the stack (`social-api`, `social-worker`, celery beat).
3. Run `alembic upgrade head` — the `talk_rooms` table must exist.
4. Activate the Talk bot in each room: on the Nextcloud host run
   `occ talk:bot:setup <bot_id> <room_token>` for every token in
   `GET /api/v1/talk-bridge/rooms`. Until then mirrors fall back to the
   app-password user and the echo filter would ignore the bot's posts.

## Daily use

1. Open Talk (Android app or desktop client). New external DM threads
   appear as `FB ·`, `IG ·`, `LI ·` rooms after the next sync (~3 min).
2. Read mirrored messages (attachments arrive as links or
   `[attachment]`).
3. Type a reply in the room — it is sent to the contact through the
   platform lane. Successful sends stay silent; failures post
   `⚠️ Send failed: <reason>`.
4. Type `mute` to pause forwarding, `unmute` to resume, `help` for a
   reminder.

## Force a sync

`POST /api/v1/talk-bridge/sync` (authenticated) — creates rooms for
current conversations and posts unseen inbound messages immediately.
`POST /talk-bridge/rooms/{token}/send` sends text through a mapped room
(team-scoped).

## Limits

- Platform 24-hour messaging windows apply (Meta/IG) — late replies
  surface as a send-failure notice.
- WhatsApp is excluded (template-window rules make blind sends fail).
- Analytics: every mirrored message and outbound reply is logged to
  `analytics_events` (`talk_bridge_inbound` / `talk_bridge_outbound`)
  and exported to the datalake.

See `docs/talk-messenger-bridge.md` for the architecture reference.
