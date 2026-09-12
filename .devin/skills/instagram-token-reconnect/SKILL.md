# Instagram Token Reconnect

Reconnect Instagram accounts with expired, invalid, or missing access tokens
through the SocialAuto OAuth flow. Detects invalid Fernet-encrypted tokens,
triggers the OAuth reconnection, and verifies the new token works.

## When to use

- Instagram polling task reports `binascii.Error: Incorrect padding` or
  `cryptography.fernet.InvalidToken`
- Instagram token refresh task reports `tokens_invalid: 1` or `errors: 1`
- Instagram Business account returns `(#3) Application does not have the
  capability to make this API call`
- Instagram Personal account token is missing or malformed
- Instagram DM bot can't read/send messages due to auth errors
- After Meta App Review grants `instagram_business_manage_messages`

## Token lifecycle

```
OAuth login → short-lived token (1 hour)
    ↓
Exchange for long-lived token (60 days)
    ↓
Auto-refresh every 7 days (instagram_token_refresh task)
    ↓
Token expires after 60 days if not refreshed
    ↓
Reconnect via OAuth (this skill)
```

## Common token errors

| Error | Cause | Fix |
|-------|-------|-----|
| `binascii.Error: Incorrect padding` | Token not properly Fernet-encrypted in DB | Reconnect via OAuth |
| `cryptography.fernet.InvalidToken` | Decrypted value is not a valid token | Reconnect via OAuth |
| `(#3) Application does not have the capability` | Missing `instagram_business_manage_messages` permission | Submit for App Review |
| `(#190) Invalid OAuth access token` | Token expired or revoked | Reconnect via OAuth |
| `(#10) Permission denied` | App not approved for the permission | Submit for App Review |

## Reconnection flow

### Step 1: Check token status

```bash
bash scripts/check-token-status.sh
```

Returns per-account status:
- `instagram_token_status`: valid, expired, invalid, refresh_failed
- `instagram_token_error`: error details
- `instagram_token_expires_at`: expiry timestamp

### Step 2: Trigger token refresh (if not already invalid)

```bash
# Manually trigger the weekly refresh task
docker compose exec -T social-worker-default celery -A app.worker.celery_app call \
  app.worker.tasks.instagram_token_refresh.refresh_instagram_tokens
```

If the token is valid but nearing expiry, this will refresh it. If the token
is invalid, it will be flagged for reconnection.

### Step 3: Reconnect via OAuth

For accounts with invalid tokens, the only fix is to re-authenticate via
the Meta OAuth flow:

1. Navigate the browser-novnc to the Instagram OAuth URL:
   ```bash
   bash scripts/reconnect-oauth.sh <account_id>
   ```
2. The user logs in to Instagram via noVNC (port 6080) if not already logged in
3. Grant the required permissions:
   - `instagram_business_basic`
   - `instagram_business_manage_messages` (requires App Review)
   - `instagram_business_content_publishing` (for posting)
4. The callback stores the new encrypted token in SocialAuto
5. Verify the new token works

### Step 4: Verify the new token

```bash
bash scripts/check-token-status.sh
```

`instagram_token_status` should now show `valid`.

## App Review dependency

The `instagram_business_manage_messages` permission requires Meta App Review
before it works for non-tester accounts. See the `meta-app-review` skill for
the submission process.

Until App Review is approved:
- Instagram Business DMs will return `(#3) Application does not have the
  capability`
- Instagram Personal DMs may work if the account is a tester

## Scripts

- `scripts/check-token-status.sh` — Check token status for all Instagram accounts
- `scripts/reconnect-oauth.sh` — Generate the OAuth URL and open it in the browser bridge
- `scripts/validate-token.sh` — Validate a specific account's token via the Graph API

## Related skills

- `meta-app-review` — Meta App Review submission for Instagram messaging permissions
- `instagram-dm` — Instagram DM API client
- `instagram-account-config` — Instagram account setup
- `socialauto-accounts` — Account management via SocialAuto API
