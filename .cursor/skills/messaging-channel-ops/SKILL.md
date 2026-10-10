---
name: messaging-channel-ops
description: >-
  What to publish on the messaging channels — the Telegram bot
  (Cloudless_newbot) and WhatsApp Business account. Covers channel posts vs
  chatbot replies, the publish_to_platform messaging dispatch, group watch,
  and Cloud API flows. Use for Telegram channel posts, WhatsApp template/
  notification sends, or chatbot work on either.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Messaging Channel Ops

Read `social-content-core` first.

## Surfaces

| Platform | Account ID | Handle | Role |
|----------|-----------|--------|------|
| telegram | `f75d0132-0964-4236-9766-a6603c997dfd` | Cloudless_newbot | Bot — channel posts + chatbot |
| whatsapp | `77f17091-3639-4633-b04d-0a3345dd7d3a` | Themistoklis Baltzakis | Business messaging + chatbot |
| viber | (connect via `/viber/connect`) | — | Bot messaging + chatbot |

## Content rules

- **Announcements, not feeds**: new-feature drops, checklist updates,
  service-status notes, newsletter links. Short, scannable, link-forward.
- These channels have no public monetization funnel — CTA can be direct
  (`https://cloudless.gr/contact`, pricing, or the monthly checklist PDF).
- Telegram channel posts count as publish targets; WhatsApp outbound needs
  approved template context or an open 24h session window — don't blast
  cold messages.

## Publishing

- `publish_to_platform` **skips** messaging platforms (`whatsapp`,
  `telegram`, `viber`) — sends go through each router's send endpoints:
  `/viber/{id}/send`, `/telegram/{id}/send`, WhatsApp send APIs.
- Viber specifics: `viber_api.py` client (`chatapi.viber.com`), broadcast
  needs Viber approval (status 15), all media must be public HTTPS URLs,
  webhook is HMAC-verified — see `viber-ops` skill.
- Telegram extended surface (`telegram_api.py`): `send_photo`/`send_video`/
  `send_document`/`send_media_group` (2-10 albums), `send_poll`,
  `send_message_with_markup` + `inline_keyboard()` CTA buttons,
  `answer_callback_query` (webhook handles `callback_query`), pin/unpin/
  delete message, `get_chat_member_count`, `export_chat_invite_link`,
  `send_chat_action`. Media accepts public HTTPS URLs or file_ids —
  caption limit is 1024 (not 4096).
- Chatbot flows live separately: `telegram_chatbot.py`,
  `telegram_group_watch.py`, `whatsapp_chatbot.py`, `whatsapp_flows.py`,
  `viber_chatbot.py` — inbound replies, not content publishing.

## Gotchas

- Telegram bot must be **channel admin** to post — if sends 403, re-check
  admin rights on the channel, not the token.
- WhatsApp template messages need prior approval in Meta Business — a
  freeform send outside the 24h window fails.
- Both are secondary channels — never block a multi-platform publish on
  them; a messaging failure shouldn't fail the whole post.

## Media capabilities

Full pipeline index: `media-pipelines` skill.

| Type | Constraint | Pipeline |
|---|---|---|
| Image | Attachment on TG channel / WA template | Brand-composed |
| Video | MP4 attachment | `branded-video-pipeline` |
| Document | PDF (e.g. monthly checklist) | `monthly-checklist-update` |
