---
name: whatsapp-ops
description: >-
  WhatsApp Business Cloud API + phone-number verification: platform setup/send flows, rate-limit-aware auto-verify, and the OTP verification runbook. Use for WhatsApp sends, verification, or Cloud API config.
---

# Whatsapp Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| WhatsApp Business Platform (Cloud API) | `whatsapp-ops` → `whatsapp-platform/` |
| WhatsApp Auto-Verify (Rate-Limit Aware) | `whatsapp-ops` → `whatsapp-auto-verify/` |
| WhatsApp Phone Number Verification | `whatsapp-ops` → `whatsapp-phone-verify/` |

## WhatsApp Business Platform (Cloud API)

Manage WhatsApp Business messaging through the Meta Cloud API. Use when
sending messages, receiving webhooks, setting up auto-reply bots, managing
business profiles, or debugging WhatsApp integration in SocialAuto. Covers
`/api/v1/whatsapp/*` endpoints.

### Architecture

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

### Meta app configuration

- **App ID**: `1936126137016578` (same Cloudless app)
- **App Dashboard**: `https://developers.facebook.com/apps/1936126137016578`
- **WhatsApp tab**: `https://developers.facebook.com/apps/1936126137016578/whatsapp/`
- **Webhook URL**: `https://social.cloudless.gr/api/v1/whatsapp/webhook`
- **Webhook verify token**: `cloudless_whatsapp_verify` (or `MESSENGER_VERIFY_TOKEN` env var)
- **Callback URL field**: `messages`

### Required permissions

| Permission | Purpose |
|------------|---------|
| `whatsapp_business_messaging` | Send messages, receive webhooks |
| `whatsapp_business_management` | Manage business profile, templates |

### Key differences from Messenger

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

### API endpoints

#### Setup & Profile

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/setup` | Set up WhatsApp Business number |
| GET | `/{account_id}/profile` | Get business profile |
| PUT | `/{account_id}/profile` | Update business profile (about, address, websites) |

#### Send API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/send` | Send text, image, or document message |
| POST | `/{account_id}/send-template` | Send template message (for initiating conversations) |

#### Phone number registration (4-step flow)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/register/create-number` | Step 1: Create a business phone number on a WABA |
| POST | `/register/request-code` | Step 2: Request verification code via SMS or voice |
| POST | `/register/verify-code` | Step 3: Verify the phone number with the code |
| POST | `/register/number` | Step 4: Register the verified number for API use |
| POST | `/register/deregister` | Deregister a phone number (stops API use) |

#### Auto-reply

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{account_id}/auto-reply` | Get auto-reply config |
| PUT | `/{account_id}/auto-reply` | Update auto-reply config |

#### Bot Builder

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{account_id}/bot/create` | Create a bot with personality preset |
| GET | `/{account_id}/bot` | Get bot config |
| PUT | `/{account_id}/bot` | Update bot config |
| POST | `/{account_id}/bot/activate` | Activate bot |
| POST | `/{account_id}/bot/deactivate` | Deactivate bot |
| GET | `/{account_id}/bot/personalities` | List personality presets |

#### Webhook

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/webhook` | Verify webhook with Meta |
| POST | `/webhook` | Receive incoming messages and status updates |

### WhatsApp account setup

#### 1. Add a WhatsApp Business number

In the Meta App Dashboard > WhatsApp > API Setup:
1. Note the **Phone Number ID** and **Access Token**
2. Add a test recipient phone number
3. Send a test message

#### 2. Configure the webhook

In the Meta App Dashboard > WhatsApp > Configuration:
1. Set **Callback URL** to `https://social.cloudless.gr/api/v1/whatsapp/webhook`
2. Set **Verify Token** to `cloudless_whatsapp_verify`
3. Subscribe to the `messages` field
4. Click "Verify and Save"

#### 3. Connect the account in SocialAuto

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

#### 4. Set up the bot

```bash
## Create a bot with professional_friendly personality
curl -X POST http://localhost:8083/api/v1/whatsapp/{account_id}/bot/create \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Cloudless Assistant", "personality": "professional_friendly", "language": "auto"}'
```

### 24-hour customer service window

WhatsApp enforces a 24-hour customer service window:
- **Inside the window**: You can send any message type (text, media) using
  `messaging_type=RESPONSE`
- **Outside the window**: You must use a pre-approved template message
  (`/send-template` endpoint)
- The window opens when a customer sends a message to your business
- The window resets with each incoming customer message

### Message types supported

| Type | Endpoint | Notes |
|------|----------|-------|
| Text | `send` with `text` | Up to 4096 characters |
| Image | `send` with `image_url` | JPEG, PNG. Max 5MB |
| Document | `send` with `document_url` | PDF, DOCX, etc. Max 100MB |
| Template | `send-template` | Pre-approved by Meta |
| Location | `send_location` (client) | Lat/long coordinates |
| Reaction | `send_reaction` (client) | Emoji on a message |

### AI auto-reply

The bot uses the same AI fallback chain as Messenger:
1. **DMR** (Docker Model Runner, local, free) — `ai/qwen3:8b-q4_K_M`
2. **Cloudflare Workers AI** (free tier) — `@cf/meta/llama-3.1-8b-instruct`
3. **Static fallback text** — configured per account

Language-aware routing:
- Greek text → Cloudflare Workers AI (handles Greek correctly)
- English/other → DMR (local, free, private)

### Files

| File | Purpose |
|------|---------|
| `app/services/whatsapp_api.py` | WhatsApp Cloud API client + webhook parser |
| `app/api/whatsapp.py` | FastAPI router with all endpoints |
| `app/api/__init__.py` | Router registration (prefix=`/whatsapp`) |
| `ARCHITECTURE.md` | Full architecture with 10 Mermaid diagrams |

### Architecture diagrams

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

### Scripts

- `scripts/check-webhook.py` — Test webhook verification endpoint
- `scripts/send-test-message.py` — Send a test message via the API
- `scripts/setup-webhook.py` — Configure webhook URL in Meta dashboard

### Related skills

- `messenger-ops` — Facebook Messenger bot (same architecture)
- `developer-apps-ops` — Meta app OAuth configuration
- `social-stack-ops` — Docker Compose stack operations

## WhatsApp Auto-Verify (Rate-Limit Aware)

Automate the WhatsApp phone verification flow with built-in rate-limit
detection, cooldown waiting, and retry logic. Handles Meta's error 136024
("wait 1 hour before trying again") by tracking the cooldown and retrying
automatically.

### When to use

- WhatsApp phone number shows `code_verification_status: NOT_VERIFIED`
- Previous code request was rate-limited (error 136024, subcode 2388091)
- You want to automate the full verify flow without manual intervention
- You need to wait for a rate-limit cooldown to expire before retrying

### Architecture

```
SocialAuto API                    Meta Cloud API
     │                                  │
     ├─ check-status.py ───────────────▶│ GET /whatsapp_phone_number
     │  (detect rate limit)             │
     │                                  │
     ├─ wait-and-request.py ──────────▶│ (waits for cooldown)
     │  (auto-wait + request code)     │ POST /request_code
     │                                  │ → SMS/voice code sent
     │                                  │
     ├─ verify-and-register.py ───────▶│ POST /verify_code
     │  (with user-provided code)       │ POST /register
     │                                  │
     └─ full-flow.py ──────────────────▶│ (orchestrates all steps)
                                        │
```

### Rate-limit handling

Meta returns error 136024 with subcode 2388091 when too many code requests
have been made. The message says "wait 1 hour before trying again."

This skill tracks the rate-limit timestamp in a state file:
`/tmp/whatsapp-verify-<account_id>.state`

When a rate-limit is detected:
1. The current timestamp + 1 hour is saved as the cooldown expiry
2. `wait-and-request.py` will sleep until the cooldown expires
3. After the cooldown, it automatically requests a new code

### Full automated flow

```bash
## Run the full flow (waits for cooldown, requests code, asks for code,
## verifies, registers)
python3 scripts/full-flow.py <account_id> [SMS|VOICE] [language] [pin]

## Example:
python3 scripts/full-flow.py 77f17091-3639-4633-b04d-0a3345dd7d3a SMS el_GR 123456
```

The script will:
1. Check current phone status
2. If rate-limited, wait for the cooldown to expire
3. Request a verification code (SMS or voice)
4. Prompt the user to enter the 6-digit code
5. Verify the code with Meta
6. Register the phone number (with the 2SV PIN if provided)
7. Confirm the phone is now VERIFIED

### Individual scripts

#### Check status and rate-limit state

```bash
python3 scripts/check-status.py <account_id>
```

Returns:
- `code_verification_status` — NOT_VERIFIED, VERIFIED, etc.
- `rate_limited` — true/false
- `cooldown_expires_at` — ISO timestamp if rate-limited
- `seconds_until_cooldown` — seconds to wait (0 if not rate-limited)

#### Wait for cooldown and request code

```bash
python3 scripts/wait-and-request.py <account_id> [SMS|VOICE] [language]
```

- Checks if rate-limited and waits for the cooldown to expire
- Requests a verification code via SMS (default) or VOICE
- Returns immediately if the code was sent successfully
- Returns exit code 1 if still rate-limited after waiting

#### Verify code and register

```bash
python3 scripts/verify-and-register.py <account_id> <6-digit-code> [6-digit-pin]
```

- Verifies the 6-digit code received by the user
- Registers the phone number for Cloud API
- The PIN is the two-step verification PIN (optional if 2SV not enabled)

#### Full flow (orchestrator)

```bash
python3 scripts/full-flow.py <account_id> [SMS|VOICE] [language] [pin]
```

Runs the complete flow:
1. check-status
2. wait-and-request
3. prompt for code
4. verify-and-register
5. confirm status

### State file

The state file at `/tmp/whatsapp-verify-<account_id>.state` contains:

```json
{
  "account_id": "<id>",
  "rate_limited_at": "2026-09-13T01:00:00Z",
  "cooldown_expires_at": "2026-09-13T02:00:00Z",
  "last_code_request_at": "2026-09-13T01:00:00Z",
  "last_error": "136024:2388091"
}
```

### Meta error reference

| Error code | Subcode | Meaning | Action |
|------------|---------|---------|--------|
| 136024 | 2388091 | "wait 1 hour" | Wait 1 hour, then retry |
| 136024 | 2388367 | "too many requests" | Wait longer, then retry |
| 10 | — | "Too many code requests" | Wait 72 hours |
| 100 | — | "Invalid code" | Request new code |
| 200 | — | "Permission error" | Check token permissions |

### Related skills

- `whatsapp-ops` — Manual verification scripts (without rate-limit handling)
- `whatsapp-ops` — Full WhatsApp Business Platform documentation
- `social-accounts-manager` — Account management via SocialAuto API

## WhatsApp Phone Number Verification

Verify and register WhatsApp Business phone numbers through the SocialAuto API
so the WhatsApp bots can send/receive messages via the Meta Cloud API.

### When to use

- WhatsApp accounts show `code_verification_status: NOT_VERIFIED`
- WhatsApp bots are configured but can't send messages
- Phone number needs to be registered for Cloud API use
- Debugging `request_code` / `verify_code` / `register` API errors
- Rate limit (136024) needs to be waited out

### Architecture

```
SocialAuto API                    Meta Cloud API
     │                                  │
     ├─ POST /phone/request-code ──────▶│ SMS/Voice code
     │                                  │ sent to phone
     │                                  │
     ├─ POST /phone/verify-code ──────▶│ Verify code
     │  (user enters code)             │
     │                                  │
     ├─ POST /phone/register ─────────▶│ Register number
     │  (with 2-step PIN)              │ for Cloud API
     │                                  │
     └─ GET /phone/status ────────────▶│ Check status
                                        │
```

Meta's Cloud API requires a 4-step phone registration flow before a number
can send/receive messages:

1. **Request verification code** — Meta sends an SMS or voice call with a
   6-digit code
2. **Verify code** — Submit the received code to Meta
3. **Register number** — Register with a 6-digit two-step verification PIN
4. **Check status** — Verify `code_verification_status: VERIFIED`

**Important:** You can only register a number via the API — you cannot
register through WhatsApp Manager UI. The UI can add/verify a number, but
the final registration call must be made via the API.

### API endpoints

All endpoints are under `/api/v1/whatsapp/{account_id}/phone/`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/request-code` | Request SMS/voice verification code |
| POST | `/verify-code` | Verify the received code |
| POST | `/register` | Register verified number for Cloud API |
| GET | `/status` | Check registration status |

#### Meta Cloud API endpoints (direct, used by SocialAuto internally)

| Method | URL | Purpose |
|--------|-----|---------|
| POST | `graph.facebook.com/v26.0/{phone_id}/request_code` | Request SMS/voice code |
| POST | `graph.facebook.com/v26.0/{phone_id}/verify_code` | Verify the code |
| POST | `graph.facebook.com/v26.0/{phone_id}/register` | Register for Cloud API |
| GET | `graph.facebook.com/v26.0/{phone_id}?fields=code_verification_status` | Check status |
| POST | `graph.facebook.com/v26.0/{phone_id}/deregister` | Deregister number |

#### Existing registration endpoints (legacy)

SocialAuto also has the original `/api/v1/whatsapp/register/*` endpoints
(which operate on the first WhatsApp account, not a specific one):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/register/request-code` | Step 2: Request code (first account) |
| POST | `/register/verify-code` | Step 3: Verify code (first account) |
| POST | `/register/number` | Step 4: Register number (first account) |
| POST | `/register/deregister` | Deregister a number |

### Prerequisites

- WhatsApp Business account connected in SocialAuto
- `access_token_enc` set (System User token from Meta Business Settings)
- `phone_number_id` in `meta_data`
- Physical access to the phone number to receive the SMS/voice code
- A 6-digit two-step verification PIN (if 2SV is enabled, or set a new one)

### Meta rate limits

| Limit | Value | Error code |
|-------|-------|------------|
| Code requests per 72h window | 10 per phone number | `136024` |
| Registration requests per 72h | 10 per phone number | `133016` |
| Deregistration per 72h | 10 per phone number | `133016` |

When rate-limited, the API returns:
```json
{"error":{"message":"Request code error","code":136024,
"error_user_msg":"You have requested a verification code too many times. Try again later."}}
```

The 72-hour window is a **moving window** from the first request. You must
wait until the oldest request in the window falls outside the 72h period.

#### Scheduled-task cooldown state (meta_data keys)

`check_whatsapp_verification` (30-min beat) persists quota state per account:

| Key | Meaning |
|-----|---------|
| `whatsapp_code_request_log` | `{phone_id: [iso_ts…]}` — every `request_code` attempt, pruned to 72h. ≥9 entries → skip (headroom under Meta's 10). |
| `whatsapp_code_status` | `sent` / `rate_limited` |
| `whatsapp_code_sent_at` + `whatsapp_code_sent_phone_id` | last successful send — no auto-resend for 24h (the beat polls status only) |
| `whatsapp_rate_limited_at` + `whatsapp_rate_limited_phone_id` | last 136024 — gates for 72h, scoped to that phone |
| `whatsapp_code_error` | last `error_user_msg` |

Cooldown keys are cleared automatically when `update_whatsapp_credentials`
changes `phone_number_id` (quotas are per-number). The status GET keeps
running every 30 min regardless — only `request_code` is gated.

### Scripts

#### Quick reference

| Script | Purpose | Interactive? |
|--------|---------|-------------|
| `check-phone-status.py` | Check current registration status | No |
| `request-code.py` | Request SMS/voice verification code | No |
| `verify-code.py` | Verify the received code | No |
| `register-phone.py` | Register the verified number | No |
| **`auto-verify.py`** | Full flow: poll → request → verify → register | **Yes** (prompts for code) |
| **`poll-rate-limit.py`** | Background poller until rate limit resets | No |

#### Step-by-step flow

##### Step 1: Check current status

```bash
python3 scripts/check-phone-status.py <account_id>
```

Returns:
- `display_phone_number` — The phone number
- `quality_rating` — Quality rating (UNKNOWN, GREEN, YELLOW, RED)
- `code_verification_status` — NOT_VERIFIED, VERIFIED, etc.

##### Step 2: Request verification code

```bash
python3 scripts/request-code.py <account_id> [SMS|VOICE] [language]
```

- `code_method`: `SMS` (default) or `VOICE`
- `language`: `en_US` (default), `el_GR`, etc.

Meta sends a 6-digit code to the phone number. The user must provide this
code in the next step.

##### Step 3: Verify the code

```bash
python3 scripts/verify-code.py <account_id> <6-digit-code>
```

The user receives the code via SMS or voice call and provides it here.

##### Step 4: Register the number

```bash
python3 scripts/register-phone.py <account_id> <6-digit-pin>
```

The PIN is the two-step verification PIN. If 2SV is not enabled, pass a
new 6-digit PIN to set it.

##### Step 5: Verify registration

```bash
python3 scripts/check-phone-status.py <account_id>
```

`code_verification_status` should now show `VERIFIED`.

#### Auto-verify (full flow in one command)

```bash
python3 scripts/auto-verify.py <account_id> [6-digit-pin] [SMS|VOICE] [language]
```

This script does the full flow:
1. Checks current status (skips if already VERIFIED)
2. Polls `request_code` every 10 minutes until the 72h rate limit resets
3. When code is sent, prompts you to enter the 6-digit code from SMS
4. Verifies the code
5. Registers the number with the PIN
6. Confirms VERIFIED status

Example:
```bash
python3 scripts/auto-verify.py 77f17091-3639-4633-b04d-0a3345dd7d3a 482913 SMS en_US
```

#### Background poller (non-interactive)

```bash
## Run in background — exits when code is successfully sent
nohup python3 scripts/poll-rate-limit.py <account_id> [SMS|VOICE] [language] &
```

This script polls `request_code` every 10 minutes until the rate limit
window resets and the code is sent. Once sent, it writes status to
`/tmp/whatsapp-verify-status.json` and exits.

After it exits, run `auto-verify.py` to complete the verify + register steps.

### Current account

| Field | Value |
|-------|-------|
| Account ID (SocialAuto) | `77f17091-3639-4633-b04d-0a3345dd7d3a` |
| Phone Number ID (Meta) | `1334613883061552` |
| WABA ID | `1073707258453499` |
| Display phone | `+30 697 777 7838` |
| Verified name | `Baltzakis Themistoklis` |
| `code_verification_status` | `NOT_VERIFIED` |
| `quality_rating` | `UNKNOWN` |
| Business verification | `not_verified` |
| Account review | `APPROVED` |
| WABA status | `ACTIVE` |
| Account mode | `LIVE` |

### Meta limits and errors

| Error code | Cause | Resolution |
|------------|-------|------------|
| `136024` | More than 10 code requests in 72 hours | Wait 72 hours (use `poll-rate-limit.py`) |
| `133010` | Account not registered | Complete verify + register steps first |
| `133016` | More than 10 register/deregister in 72h | Wait 72 hours |
| `(#100) Invalid code` | Wrong code entered | Request a new code and try again |
| `(#100) Phone number not verified` | Code not verified before register | Complete verify-code step first |
| `(#200) Permission error` | Token lacks permissions | Use a System User token with WhatsApp permissions |

### Official documentation

- [Business phone numbers](https://developers.facebook.com/docs/whatsapp/cloud-api/phone-numbers)
- [Register a business phone number](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/registration)
- [Request Code API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/phone-number-verification-request-code-api)
- [Verify Code API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/verify-code-api)
- [WhatsApp Manager](https://business.facebook.com/latest/whatsapp_manager/)

### Related skills

- `whatsapp-ops` — Full WhatsApp Business Platform documentation
- `whatsapp-ops` — Automated verification with retry logic
- `social-stack-ops` — Docker Compose stack operations
- `social-accounts-manager` — Account management via SocialAuto API
