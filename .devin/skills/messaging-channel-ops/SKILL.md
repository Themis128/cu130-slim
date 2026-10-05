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

## Content rules

- **Announcements, not feeds**: new-feature drops, checklist updates,
  service-status notes, newsletter links. Short, scannable, link-forward.
- These channels have no public monetization funnel — CTA can be direct
  (`https://cloudless.gr/contact`, pricing, or the monthly checklist PDF).
- Telegram channel posts count as publish targets; WhatsApp outbound needs
  approved template context or an open 24h session window — don't blast
  cold messages.

## Publishing

- `publish_to_platform` dispatches messaging posts to the channel:
  `telegram` → `TelegramAPIClient` (bot token, `telegram_api.py`);
  `whatsapp` → `whatsapp_cloud_client.py` (Cloud API).
- Chatbot flows live separately: `telegram_chatbot.py`,
  `telegram_group_watch.py`, `whatsapp_chatbot.py`, `whatsapp_flows.py` —
  inbound replies, not content publishing.

## Gotchas

- Telegram bot must be **channel admin** to post — if sends 403, re-check
  admin rights on the channel, not the token.
- WhatsApp template messages need prior approval in Meta Business — a
  freeform send outside the 24h window fails.
- Both are secondary channels — never block a multi-platform publish on
  them; a messaging failure shouldn't fail the whole post.
