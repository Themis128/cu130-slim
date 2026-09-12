# Instagram Personal Account Reconnect

Reconnect Instagram personal accounts that have invalid or missing OAuth
tokens by using the instagrapi private mobile API sidecar. This bypasses
the need for Meta App Review (which is required for the Graph API path)
and works for personal, creator, and business accounts.

## When to use

- Instagram personal account has `Invalid Fernet token` or decryption
  failure when the token refresh task runs
- The account was restored from D1 backup with a corrupted/missing token
- The Graph API OAuth flow doesn't cover personal accounts (it stores a
  Facebook User Access Token, not an Instagram token)
- You need the account working for the Instagram DM polling task
- You want to use the private API for publishing (not just messaging)

## Architecture

```
SocialAuto API                instagrapi sidecar
     │                              │
     ├─ get-credentials.sh ────────▶│ (reads from secret store)
     │                              │
     ├─ login.sh ──────────────────▶│ POST /auth/login
     │  (username + password)       │ → session_id
     │                              │
     ├─ handle-2fa.sh ─────────────▶│ POST /auth/login
     │  (if 2FA required)           │  + verification_code
     │                              │
     ├─ handle-challenge.sh ───────▶│ POST /auth/challenge/resolve
     │  (if challenge required)     │  + security_code
     │                              │
     ├─ save-session.sh ──────────▶│ GET /auth/settings
     │  (persist to SocialAuto)    │ → settings JSON
     │                              │
     └─ verify.sh ────────────────▶│ GET /auth/profile
                                    │ → account info
```

The instagrapi sidecar runs at `http://localhost:8011` (host) or
`http://instagram-private-api:8000` (Docker internal). It uses the
`instagrapi` Python library to simulate the Instagram mobile app API.

## Prerequisites

- Instagram credentials stored in SocialAuto's secret store
- The `instagram-private-api` container running and healthy
- Cloudflare WARP proxy configured (recommended to avoid IP blocks)

## Full automated flow

```bash
# Run the complete reconnect flow
bash scripts/reconnect.sh <account_id>
```

This will:
1. Check the sidecar health
2. Retrieve Instagram credentials from the secret store
3. Attempt login via the sidecar
4. Handle 2FA or challenge if required (prompts for code)
5. Save the session to SocialAuto
6. Verify the session is active

## Individual scripts

### Check sidecar health

```bash
bash scripts/check-sidecar.sh
```

Returns exit code 0 if the sidecar is healthy and running.

### Get stored credentials

```bash
bash scripts/get-credentials.sh <account_id>
```

Retrieves the Instagram username and password from SocialAuto's secret
store. Does not print the password to stdout (uses a temp file).

### Login via sidecar

```bash
bash scripts/login.sh <account_id>
```

Attempts to log in using the stored credentials. Returns:
- `session_id` on success
- `challenge_required` if a security code is needed
- `two_factor_required` if 2FA is needed

### Handle 2FA

```bash
bash scripts/handle-2fa.sh <account_id> <verification_code>
```

Completes 2FA login with a TOTP or SMS code.

### Handle challenge

```bash
bash scripts/handle-challenge.sh <session_id> <last_json> <security_code>
```

Resolves a challenge (SMS/email verification) with a security code.

### Save session to SocialAuto

```bash
bash scripts/save-session.sh <account_id> <session_id>
```

Saves the session_id and settings JSON to the account's `meta_data` in
the SocialAuto database so it persists across restarts.

### Verify session

```bash
bash scripts/verify.sh <account_id>
```

Checks if the saved session is active by calling the sidecar's profile
endpoint.

## Session persistence

After a successful login:
1. The sidecar stores the session in `/data/db.json` (persisted via
   the `instagram_sessions` Docker volume)
2. SocialAuto saves `session_id` and `settings` JSON in
   `social_accounts.meta_data.private_api_session_id` and
   `social_accounts.meta_data.private_api_settings`
3. On restart, the session can be restored without re-entering the
   password using `PATCH /auth/settings`

## Sessionid import (alternative to password login)

If the password login fails with `UnknownError` and a Greek message about
"updating the app", Instagram is rejecting the instagrapi API version.
Use the sessionid import instead:

1. Get the `sessionid` cookie from a logged-in Instagram browser session
   (Playwright MCP or browser-novnc)
2. Import it into the sidecar:

```bash
bash scripts/import-sessionid.sh <account_id> <sessionid>
```

This bypasses the password login entirely and uses the existing browser
session. The sessionid can be extracted from:
- Playwright MCP: `document.cookie` on instagram.com
- browser-novnc: `GET /session/cookies` on the bridge (port 9223)
- Chrome DevTools: Application → Cookies → instagram.com → sessionid

## Important notes

- Instagram aggressively blocks datacenter IPs. The Cloudflare WARP
  proxy (`socks5://warp-proxy:1080`) is used by default.
- The `challenge_required` error means Instagram sent a security code
  via SMS or email. The account owner must provide that code.
- The `two_factor_required` error means 2FA is enabled. The user must
  provide a TOTP or SMS code.
- **Password login may fail** with `UnknownError` if the instagrapi
  library version doesn't match Instagram's current API version. Use
  the sessionid import as a fallback.
- Sessions stored in the sidecar persist across container restarts.
- Never log or commit session IDs, settings JSON, or passwords.

## Related skills

- `instagram-private-api` — Full instagrapi sidecar documentation
- `instagram-token-reconnect` — OAuth reconnection for Business accounts
- `instagram-account-config` — Account configuration and bio updates
- `socialauto-accounts` — Account management via SocialAuto API
