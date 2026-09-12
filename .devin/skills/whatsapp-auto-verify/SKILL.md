# WhatsApp Auto-Verify (Rate-Limit Aware)

Automate the WhatsApp phone verification flow with built-in rate-limit
detection, cooldown waiting, and retry logic. Handles Meta's error 136024
("wait 1 hour before trying again") by tracking the cooldown and retrying
automatically.

## When to use

- WhatsApp phone number shows `code_verification_status: NOT_VERIFIED`
- Previous code request was rate-limited (error 136024, subcode 2388091)
- You want to automate the full verify flow without manual intervention
- You need to wait for a rate-limit cooldown to expire before retrying

## Architecture

```
SocialAuto API                    Meta Cloud API
     │                                  │
     ├─ check-status.sh ───────────────▶│ GET /whatsapp_phone_number
     │  (detect rate limit)             │
     │                                  │
     ├─ wait-and-request.sh ──────────▶│ (waits for cooldown)
     │  (auto-wait + request code)     │ POST /request_code
     │                                  │ → SMS/voice code sent
     │                                  │
     ├─ verify-and-register.sh ───────▶│ POST /verify_code
     │  (with user-provided code)       │ POST /register
     │                                  │
     └─ full-flow.sh ──────────────────▶│ (orchestrates all steps)
                                        │
```

## Rate-limit handling

Meta returns error 136024 with subcode 2388091 when too many code requests
have been made. The message says "wait 1 hour before trying again."

This skill tracks the rate-limit timestamp in a state file:
`/tmp/whatsapp-verify-<account_id>.state`

When a rate-limit is detected:
1. The current timestamp + 1 hour is saved as the cooldown expiry
2. `wait-and-request.sh` will sleep until the cooldown expires
3. After the cooldown, it automatically requests a new code

## Full automated flow

```bash
# Run the full flow (waits for cooldown, requests code, asks for code,
# verifies, registers)
bash scripts/full-flow.sh <account_id> [SMS|VOICE] [language] [pin]

# Example:
bash scripts/full-flow.sh 77f17091-3639-4633-b04d-0a3345dd7d3a SMS el_GR 123456
```

The script will:
1. Check current phone status
2. If rate-limited, wait for the cooldown to expire
3. Request a verification code (SMS or voice)
4. Prompt the user to enter the 6-digit code
5. Verify the code with Meta
6. Register the phone number (with the 2SV PIN if provided)
7. Confirm the phone is now VERIFIED

## Individual scripts

### Check status and rate-limit state

```bash
bash scripts/check-status.sh <account_id>
```

Returns:
- `code_verification_status` — NOT_VERIFIED, VERIFIED, etc.
- `rate_limited` — true/false
- `cooldown_expires_at` — ISO timestamp if rate-limited
- `seconds_until_cooldown` — seconds to wait (0 if not rate-limited)

### Wait for cooldown and request code

```bash
bash scripts/wait-and-request.sh <account_id> [SMS|VOICE] [language]
```

- Checks if rate-limited and waits for the cooldown to expire
- Requests a verification code via SMS (default) or VOICE
- Returns immediately if the code was sent successfully
- Returns exit code 1 if still rate-limited after waiting

### Verify code and register

```bash
bash scripts/verify-and-register.sh <account_id> <6-digit-code> [6-digit-pin]
```

- Verifies the 6-digit code received by the user
- Registers the phone number for Cloud API
- The PIN is the two-step verification PIN (optional if 2SV not enabled)

### Full flow (orchestrator)

```bash
bash scripts/full-flow.sh <account_id> [SMS|VOICE] [language] [pin]
```

Runs the complete flow:
1. check-status
2. wait-and-request
3. prompt for code
4. verify-and-register
5. confirm status

## State file

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

## Meta error reference

| Error code | Subcode | Meaning | Action |
|------------|---------|---------|--------|
| 136024 | 2388091 | "wait 1 hour" | Wait 1 hour, then retry |
| 136024 | 2388367 | "too many requests" | Wait longer, then retry |
| 10 | — | "Too many code requests" | Wait 72 hours |
| 100 | — | "Invalid code" | Request new code |
| 200 | — | "Permission error" | Check token permissions |

## Related skills

- `whatsapp-phone-verify` — Manual verification scripts (without rate-limit handling)
- `whatsapp-platform` — Full WhatsApp Business Platform documentation
- `socialauto-accounts` — Account management via SocialAuto API
