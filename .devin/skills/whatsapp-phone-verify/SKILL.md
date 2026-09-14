# WhatsApp Phone Number Verification

Verify and register WhatsApp Business phone numbers through the SocialAuto API
so the WhatsApp bots can send/receive messages via the Meta Cloud API.

## When to use

- WhatsApp accounts show `code_verification_status: NOT_VERIFIED`
- WhatsApp bots are configured but can't send messages
- Phone number needs to be registered for Cloud API use
- Debugging `request_code` / `verify_code` / `register` API errors
- Rate limit (136024) needs to be waited out

## Architecture

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

## API endpoints

All endpoints are under `/api/v1/whatsapp/{account_id}/phone/`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/request-code` | Request SMS/voice verification code |
| POST | `/verify-code` | Verify the received code |
| POST | `/register` | Register verified number for Cloud API |
| GET | `/status` | Check registration status |

### Meta Cloud API endpoints (direct, used by SocialAuto internally)

| Method | URL | Purpose |
|--------|-----|---------|
| POST | `graph.facebook.com/v26.0/{phone_id}/request_code` | Request SMS/voice code |
| POST | `graph.facebook.com/v26.0/{phone_id}/verify_code` | Verify the code |
| POST | `graph.facebook.com/v26.0/{phone_id}/register` | Register for Cloud API |
| GET | `graph.facebook.com/v26.0/{phone_id}?fields=code_verification_status` | Check status |
| POST | `graph.facebook.com/v26.0/{phone_id}/deregister` | Deregister number |

### Existing registration endpoints (legacy)

SocialAuto also has the original `/api/v1/whatsapp/register/*` endpoints
(which operate on the first WhatsApp account, not a specific one):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/register/request-code` | Step 2: Request code (first account) |
| POST | `/register/verify-code` | Step 3: Verify code (first account) |
| POST | `/register/number` | Step 4: Register number (first account) |
| POST | `/register/deregister` | Deregister a number |

## Prerequisites

- WhatsApp Business account connected in SocialAuto
- `access_token_enc` set (System User token from Meta Business Settings)
- `phone_number_id` in `meta_data`
- Physical access to the phone number to receive the SMS/voice code
- A 6-digit two-step verification PIN (if 2SV is enabled, or set a new one)

## Meta rate limits

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

## Scripts

### Quick reference

| Script | Purpose | Interactive? |
|--------|---------|-------------|
| `check-phone-status.sh` | Check current registration status | No |
| `request-code.sh` | Request SMS/voice verification code | No |
| `verify-code.sh` | Verify the received code | No |
| `register-phone.sh` | Register the verified number | No |
| **`auto-verify.sh`** | Full flow: poll → request → verify → register | **Yes** (prompts for code) |
| **`poll-rate-limit.sh`** | Background poller until rate limit resets | No |

### Step-by-step flow

#### Step 1: Check current status

```bash
bash scripts/check-phone-status.sh <account_id>
```

Returns:
- `display_phone_number` — The phone number
- `quality_rating` — Quality rating (UNKNOWN, GREEN, YELLOW, RED)
- `code_verification_status` — NOT_VERIFIED, VERIFIED, etc.

#### Step 2: Request verification code

```bash
bash scripts/request-code.sh <account_id> [SMS|VOICE] [language]
```

- `code_method`: `SMS` (default) or `VOICE`
- `language`: `en_US` (default), `el_GR`, etc.

Meta sends a 6-digit code to the phone number. The user must provide this
code in the next step.

#### Step 3: Verify the code

```bash
bash scripts/verify-code.sh <account_id> <6-digit-code>
```

The user receives the code via SMS or voice call and provides it here.

#### Step 4: Register the number

```bash
bash scripts/register-phone.sh <account_id> <6-digit-pin>
```

The PIN is the two-step verification PIN. If 2SV is not enabled, pass a
new 6-digit PIN to set it.

#### Step 5: Verify registration

```bash
bash scripts/check-phone-status.sh <account_id>
```

`code_verification_status` should now show `VERIFIED`.

### Auto-verify (full flow in one command)

```bash
bash scripts/auto-verify.sh <account_id> [6-digit-pin] [SMS|VOICE] [language]
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
bash scripts/auto-verify.sh 77f17091-3639-4633-b04d-0a3345dd7d3a 482913 SMS en_US
```

### Background poller (non-interactive)

```bash
# Run in background — exits when code is successfully sent
nohup bash scripts/poll-rate-limit.sh <account_id> [SMS|VOICE] [language] &
```

This script polls `request_code` every 10 minutes until the rate limit
window resets and the code is sent. Once sent, it writes status to
`/tmp/whatsapp-verify-status.json` and exits.

After it exits, run `auto-verify.sh` to complete the verify + register steps.

## Current account

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

## Meta limits and errors

| Error code | Cause | Resolution |
|------------|-------|------------|
| `136024` | More than 10 code requests in 72 hours | Wait 72 hours (use `poll-rate-limit.sh`) |
| `133010` | Account not registered | Complete verify + register steps first |
| `133016` | More than 10 register/deregister in 72h | Wait 72 hours |
| `(#100) Invalid code` | Wrong code entered | Request a new code and try again |
| `(#100) Phone number not verified` | Code not verified before register | Complete verify-code step first |
| `(#200) Permission error` | Token lacks permissions | Use a System User token with WhatsApp permissions |

## Official documentation

- [Business phone numbers](https://developers.facebook.com/docs/whatsapp/cloud-api/phone-numbers)
- [Register a business phone number](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/registration)
- [Request Code API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/phone-number-verification-request-code-api)
- [Verify Code API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/verify-code-api)
- [WhatsApp Manager](https://business.facebook.com/latest/whatsapp_manager/)

## Related skills

- `whatsapp-platform` — Full WhatsApp Business Platform documentation
- `whatsapp-auto-verify` — Automated verification with retry logic
- `social-stack-ops` — Docker Compose stack operations
- `socialauto-accounts` — Account management via SocialAuto API
