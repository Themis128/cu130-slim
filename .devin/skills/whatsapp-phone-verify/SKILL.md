# WhatsApp Phone Number Verification

Verify and register WhatsApp Business phone numbers through the SocialAuto API
so the WhatsApp bots can send/receive messages via the Meta Cloud API.

## When to use

- WhatsApp accounts show `code_verification_status: NOT_VERIFIED`
- WhatsApp bots are configured but can't send messages
- Phone number needs to be registered for Cloud API use
- Debugging `request_code` / `verify_code` / `register` API errors

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

## API endpoints

All endpoints are under `/api/v1/whatsapp/{account_id}/phone/`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/request-code` | Request SMS/voice verification code |
| POST | `/verify-code` | Verify the received code |
| POST | `/register` | Register verified number for Cloud API |
| GET | `/status` | Check registration status |

### Existing registration endpoints

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
- A 6-digit two-step verification PIN (if 2SV is enabled)

## Registration flow

### Step 1: Check current status

```bash
bash scripts/check-phone-status.sh <account_id>
```

Returns:
- `display_phone_number` — The phone number
- `quality_rating` — Quality rating (UNKNOWN, GREEN, YELLOW, RED)
- `code_verification_status` — NOT_VERIFIED, VERIFIED, etc.

### Step 2: Request verification code

```bash
bash scripts/request-code.sh <account_id> [SMS|VOICE] [language]
```

- `code_method`: `SMS` (default) or `VOICE`
- `language`: `en_US` (default), `el_GR`, etc.

Meta sends a 6-digit code to the phone number. The user must provide this
code in the next step.

### Step 3: Verify the code

```bash
bash scripts/verify-code.sh <account_id> <6-digit-code>
```

The user receives the code via SMS or voice call and provides it here.

### Step 4: Register the number

```bash
bash scripts/register-phone.sh <account_id> <6-digit-pin>
```

The PIN is the two-step verification PIN. If 2SV is not enabled, pass an
empty string or the PIN you want to set.

### Step 5: Verify registration

```bash
bash scripts/check-phone-status.sh <account_id>
```

`code_verification_status` should now show `VERIFIED`.

## Meta limits and errors

| Error | Cause | Resolution |
|-------|-------|------------|
| `(#10) Too many code requests` | More than 10 code requests in 72 hours | Wait 72 hours before requesting again |
| `(#100) Invalid code` | Wrong code entered | Request a new code and try again |
| `(#100) Phone number not verified` | Code not verified before register | Complete verify-code step first |
| `Throttling` | Too many requests | Wait and retry |
| `(#200) Permission error` | Token lacks permissions | Use a System User token with WhatsApp permissions |

## Scripts

- `scripts/check-phone-status.sh` — Check phone registration status
- `scripts/request-code.sh` — Request SMS/voice verification code
- `scripts/verify-code.sh` — Verify the received code
- `scripts/register-phone.sh` — Register the verified number

## Related skills

- `whatsapp-platform` — Full WhatsApp Business Platform documentation
- `social-stack-ops` — Docker Compose stack operations
- `socialauto-accounts` — Account management via SocialAuto API
