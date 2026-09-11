# WhatsApp Business Platform (Cloud API)

Manage WhatsApp Business messaging through the Meta Cloud API. Use when
sending messages, receiving webhooks, setting up auto-reply bots, managing
business profiles, or debugging WhatsApp integration in SocialAuto. Covers
`/api/v1/whatsapp/*` endpoints.

## Architecture

```
Customer → WhatsApp → Meta webhook → SocialAuto /api/v1/whatsapp/webhook
                                              ↓
                                    parse_webhook_event()
                                              ↓
                                    _process_inline()
                                              ↓
                                    AI reply (DMR → Cloudflare)
                                              ↓
                                    WhatsAppAPIClient.send_text()
                                              ↓
                                    Customer receives reply
```

WhatsApp Business Cloud API uses the same Meta app as Messenger, but with
different permissions and webhook fields.

## Meta app configuration

- **App ID**: `1936126137016578` (same Cloudless app)
- **App Dashboard**: `https://developers.facebook.com/apps/1936126137016578`
- **WhatsApp tab**: `https://developers.facebook.com/apps/1936126137016578/whatsapp/`
- **Webhook URL**: `https://social.cloudless.gr/api/v1/whatsapp/webhook`
- **Webhook verify token**: `cloudless_whatsapp_verify` (or `MESSENGER_VERIFY_TOKEN` env var)
- **Callback URL field**: `messages`

## Required permissions

| Permission | Purpose |
|------------|---------|
| `whatsapp_business_messaging` | Send messages, receive webhooks |
| `whatsapp_business_management` | Manage business profile, templates |

## Key differences from Messenger

| Aspect | Messenger | WhatsApp |
|--------|-----------|----------|
| Recipient ID | PSID (Page-Scoped ID) | Phone number (E.164) |
| Initiate conversation | Any time | Template message required (outside 24h window) |
| 24-hour window | Customer service window | Customer service window |
| Webhook object | `page` | `whatsapp_business_account` |
| Webhook fields | `messages`, `messaging_postbacks` | `messages` |
| Signature header | `X-Hub-Signature-256` | `X-Hub-Signature-256` (same) |
| App secret | `FACEBOOK_APP_SECRET` | `FACEBOOK_APP_SECRET` (same) |
| Send endpoint | `/{page_id}/messages` | `/{phone_number_id}/messages` |
| Profile API | `/{page_id}/messenger_profile` | `/{phone_number_id}/whatsapp_business_profile` |

## API endpoints

### Setup & Profile

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/setup` | Set up WhatsApp Business number |
| GET | `/{account_id}/profile` | Get business profile |
| PUT | `/{account_id}/profile` | Update business profile (about, address, websites) |

### Send API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/send` | Send text, image, or document message |
| POST | `/{account_id}/send-template` | Send template message (for initiating conversations) |

### Phone number registration (4-step flow)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/register/create-number` | Step 1: Create a business phone number on a WABA |
| POST | `/register/request-code` | Step 2: Request verification code via SMS or voice |
| POST | `/register/verify-code` | Step 3: Verify the phone number with the code |
| POST | `/register/number` | Step 4: Register the verified number for API use |
| POST | `/register/deregister` | Deregister a phone number (stops API use) |

### Auto-reply

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{account_id}/auto-reply` | Get auto-reply config |
| PUT | `/{account_id}/auto-reply` | Update auto-reply config |

### Bot Builder

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/bot/create` | Create a bot with personality preset |
| GET | `/{account_id}/bot` | Get bot config |
| PUT | `/{account_id}/bot` | Update bot config |
| POST | `/{account_id}/bot/activate` | Activate bot |
| POST | `/{account_id}/bot/deactivate` | Deactivate bot |
| GET | `/{account_id}/bot/personalities` | List personality presets |

### Webhook

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/webhook` | Verify webhook with Meta |
| POST | `/webhook` | Receive incoming messages and status updates |

## WhatsApp account setup

### 1. Add a WhatsApp Business number

In the Meta App Dashboard > WhatsApp > API Setup:
1. Note the **Phone Number ID** and **Access Token**
2. Add a test recipient phone number
3. Send a test message

### 2. Configure the webhook

In the Meta App Dashboard > WhatsApp > Configuration:
1. Set **Callback URL** to `https://social.cloudless.gr/api/v1/whatsapp/webhook`
2. Set **Verify Token** to `cloudless_whatsapp_verify`
3. Subscribe to the `messages` field
4. Click "Verify and Save"

### 3. Connect the account in SocialAuto

Create a WhatsApp account in SocialAuto with:
```json
{
  "platform": "whatsapp",
  "account_type": "business",
  "display_name": "Cloudless WhatsApp",
  "meta_data": {
    "phone_number_id": "<from Meta dashboard>",
    "access_token": "<from Meta dashboard>",
    "display_phone_number": "+30..."
  }
}
```

### 4. Set up the bot

```bash
# Create a bot with professional_friendly personality
curl -X POST http://localhost:8083/api/v1/whatsapp/{account_id}/bot/create \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Cloudless Assistant", "personality": "professional_friendly", "language": "auto"}'
```

## 24-hour customer service window

WhatsApp enforces a 24-hour customer service window:
- **Inside the window**: You can send any message type (text, media) using
  `messaging_type=RESPONSE`
- **Outside the window**: You must use a pre-approved template message
  (`/send-template` endpoint)
- The window opens when a customer sends a message to your business
- The window resets with each incoming customer message

## Message types supported

| Type | Endpoint | Notes |
|------|----------|-------|
| Text | `send` with `text` | Up to 4096 characters |
| Image | `send` with `image_url` | JPEG, PNG. Max 5MB |
| Document | `send` with `document_url` | PDF, DOCX, etc. Max 100MB |
| Template | `send-template` | Pre-approved by Meta |
| Location | `send_location` (client) | Lat/long coordinates |
| Reaction | `send_reaction` (client) | Emoji on a message |

## AI auto-reply

The bot uses the same AI fallback chain as Messenger:
1. **DMR** (Docker Model Runner, local, free) — `ai/qwen3:8b-q4_K_M`
2. **Cloudflare Workers AI** (free tier) — `@cf/meta/llama-3.1-8b-instruct`
3. **Static fallback text** — configured per account

Language-aware routing:
- Greek text → Cloudflare Workers AI (handles Greek correctly)
- English/other → DMR (local, free, private)

## Files

| File | Purpose |
|------|---------|
| `app/services/whatsapp_api.py` | WhatsApp Cloud API client + webhook parser |
| `app/api/whatsapp.py` | FastAPI router with all endpoints |
| `app/api/__init__.py` | Router registration (prefix=`/whatsapp`) |
| `ARCHITECTURE.md` | Full architecture with 10 Mermaid diagrams |

## Architecture diagrams

See `ARCHITECTURE.md` for:
- System overview
- Registration flow (4-step sequence)
- Webhook message flow
- Bot lifecycle state diagram
- File architecture
- Endpoint map
- AI reply decision tree
- Data model (ER diagram)
- Messenger vs WhatsApp comparison
- Deployment topology

## Scripts

- `scripts/check-webhook.sh` — Test webhook verification endpoint
- `scripts/send-test-message.sh` — Send a test message via the API
- `scripts/setup-webhook.sh` — Configure webhook URL in Meta dashboard

## Related skills

- `messenger-platform` — Facebook Messenger bot (same architecture)
- `meta-oauth-setup` — Meta app OAuth configuration
- `social-stack-ops` — Docker Compose stack operations
