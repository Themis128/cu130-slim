# Social Session Bootstrap — Master Guide

End-to-end guide for bootstrapping all social media bot sessions through
SocialAuto. Covers every manual step needed to activate 24/7 bot coverage
across all platforms, with the Playwright MCP server as the primary login
mechanism.

## When to use

- Setting up SocialAuto bots for the first time
- After a platform session has expired and bots stopped responding
- After adding a new social account that needs login/verification
- Debugging why a specific platform's DM bot isn't responding

## Prerequisites

1. SocialAuto stack running (`docker compose ps` shows all containers healthy)
2. Playwright MCP server configured in `.devin/mcp_config.json`
3. Browser-novnc container running (port 9223 bridge, port 6080 noVNC)
4. Admin credentials in `.env` (`SOCIAL_ADMIN_EMAIL` / `SOCIAL_ADMIN_PASSWORD`)
5. SocialAuto API healthy (`curl http://localhost:8083/health`)

## Platform activation checklist

| # | Platform | Method | Skill | Status after setup |
|---|----------|--------|-------|-------------------|
| 1 | Twitter/X | Playwright MCP login → cookie inject | `playwright-mcp-login` | DM polling every 5 min |
| 2 | TikTok | Playwright MCP login → cookie inject | `playwright-mcp-login` | DM polling every 5 min |
| 3 | Threads | Playwright MCP login → cookie inject | `playwright-mcp-login` | DM polling every 3 min |
| 4 | Instagram | OAuth reconnect + App Review | `instagram-token-reconnect` | DM polling every 3 min |
| 5 | WhatsApp | Phone verification via API | `whatsapp-phone-verify` | Webhook-based 24/7 |
| 6 | LinkedIn | Sidecar session refresh | `linkedin-sidecar-ops` | DM polling every 6 min |
| 7 | Facebook Page | Webhook setup | `messenger-platform` | Webhook-based 24/7 |
| 8 | Facebook Personal | Browser bridge login | `browser-bridge-ops` | DM polling every 2 min |

## Bootstrap order

Run these in order — some steps depend on earlier ones:

### Phase 1: Browser session logins (Twitter, TikTok, Threads)

These use the Playwright MCP server to log in and inject cookies into the
browser-novnc container.

```bash
# 1a. Twitter/X
# Use the playwright-mcp-login skill:
#   1. Navigate to https://x.com/i/flow/login
#   2. Fill email + password (from SocialAuto secrets)
#   3. Handle 2FA if needed
#   4. Extract cookies
#   5. Inject into browser-novnc
#   6. Verify session
bash .devin/skills/playwright-mcp-login/scripts/check-session.sh twitter

# 1b. TikTok
# Use the playwright-mcp-login skill:
#   1. Navigate to https://www.tiktok.com/login/phone-or-email/email
#   2. Fill email + password
#   3. Handle captcha if needed
#   4. Extract + inject cookies
#   5. Verify session
bash .devin/skills/playwright-mcp-login/scripts/check-session.sh tiktok

# 1c. Threads (uses Instagram credentials)
# Use the playwright-mcp-login skill:
#   1. Navigate to https://www.threads.com/login
#   2. Fill Instagram username + password
#   3. Handle 2FA if needed
#   4. Extract + inject cookies
#   5. Verify session
bash .devin/skills/playwright-mcp-login/scripts/check-session.sh threads
```

### Phase 2: Instagram OAuth reconnection

```bash
# 2a. Check token status
bash .devin/skills/instagram-token-reconnect/scripts/check-token-status.sh

# 2b. Reconnect invalid tokens via OAuth
bash .devin/skills/instagram-token-reconnect/scripts/reconnect-oauth.sh <account_id>

# 2c. Validate the new token
bash .devin/skills/instagram-token-reconnect/scripts/validate-token.sh <account_id>
```

### Phase 3: WhatsApp phone verification

```bash
# 3a. Check current status
bash .devin/skills/whatsapp-phone-verify/scripts/check-phone-status.sh <account_id>

# 3b. Request verification code (SMS or voice)
bash .devin/skills/whatsapp-phone-verify/scripts/request-code.sh <account_id> SMS el_GR

# 3c. Verify the code (user provides the 6-digit code from SMS)
bash .devin/skills/whatsapp-phone-verify/scripts/verify-code.sh <account_id> 123456

# 3d. Register the number
bash .devin/skills/whatsapp-phone-verify/scripts/register-phone.sh <account_id> 123456

# 3e. Verify registration
bash .devin/skills/whatsapp-phone-verify/scripts/check-phone-status.sh <account_id>
```

### Phase 4: LinkedIn session refresh

```bash
# 4a. Check sidecar session
curl -s http://localhost:9225/session/validate | python3 -m json.tool

# 4b. Trigger session refresh task
docker compose exec -T social-worker-default celery -A app.worker.celery_app call \
  app.worker.tasks.linkedin_session_refresh.refresh_linkedin_sessions

# 4c. If rate-limited, wait for cooldown (6h) or inject session manually
# See linkedin-sidecar-ops skill
```

### Phase 5: Verify all polling tasks

After all sessions are active, trigger each polling task manually to verify
they can now read/send messages:

```bash
# Trigger all 6 polling tasks
docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.personal_messenger.poll_personal_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.instagram_messenger.poll_instagram_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.threads_messenger.poll_threads_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.twitter_messenger.poll_twitter_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.tiktok_messenger.poll_tiktok_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.linkedin_messenger.poll_linkedin_messenger
```

## Account IDs reference

| Platform | Account ID (prefix) | Display name |
|----------|---------------------|-------------|
| Instagram Business | c1e2d99b | @cloudless.gr |
| Instagram Personal | d4e670ac | Themistoklis |
| LinkedIn Personal | 2de16fca | Themistoklis |
| LinkedIn Business | 58706dd3 | cloudless.gr |
| Twitter | 8af5fe23 | Themistoklis |
| TikTok | 08418574 | cloudless.gr |
| WhatsApp 1 | 77f17091 | Cloudless 1 |
| Threads | 96e9adaa | Cloudless |

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| Polling task skips account | Browser session not active | Run login via Playwright MCP |
| `InvalidToken` / `Incorrect padding` | Instagram token corrupted | Reconnect via OAuth |
| `403 Forbidden` | Twitter API tier / session | Use browser bridge instead of API |
| `404 Not Found` | TikTok API not in EU | Use browser bridge instead of API |
| `NOT_VERIFIED` | WhatsApp phone not verified | Run phone verification flow |
| Rate-limited | LinkedIn session cooldown | Wait 6h or inject fresh session |
| `(#3) Application does not have capability` | Missing App Review permission | Submit for Meta App Review |

## Related skills

- `playwright-mcp-login` — Login to platforms via Playwright MCP
- `whatsapp-phone-verify` — WhatsApp phone verification
- `instagram-token-reconnect` — Instagram OAuth reconnection
- `browser-bridge-ops` — Browser-novnc bridge operations
- `linkedin-sidecar-ops` — LinkedIn sidecar operations
- `threads-ops` — Threads account management
- `meta-app-review` — Meta App Review submission
- `social-stack-ops` — Docker Compose stack operations
