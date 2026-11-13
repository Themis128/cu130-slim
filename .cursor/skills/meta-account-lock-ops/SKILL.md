---
name: meta-account-lock-ops
description: >-
  Diagnose and escalate Meta (Facebook/Instagram/Threads/WhatsApp)
  account-lock and checkpoint gates in SocialAuto. Covers the "Unlock your
  Meta Account" email lock (no one can log in — new logins only), the
  two_factor_required sidecar checkpoint, trusted-device "Was this you?"
  approval via push notification, login-alert enablement, and the heal-bot
  cooldown that makes prompts appear intermittent. Use when Meta accounts
  flip to revoked/expired, publishes fail with OAuthException 190/459,
  or the user reports no approval prompt on their phone.
allowed-tools:
  - read
  - exec
  - grep
triggers:
  - user
  - model
---

# Meta Account Lock Ops

Read `human-gate-ops` and `session-persistence-ops` first — this skill is
the Meta-specific failure class underneath them.

## The failure class (verified 2026-11-13)

Meta sends `security@facebookmail.com` → **"Unlock your Meta Account"**:
*"We've locked your account, which means no one can log in or view any of
the profiles in your account."* This locks **new logins only** — existing
app sessions (mobile, desktop) keep working. Every Meta-family token in
SocialAuto derives from that user credential, so `debug_token` sweeps
flip facebook/instagram/threads/whatsapp/messenger rows to `revoked`
en masse.

## Diagnostic — one shot

```bash
python3 scripts/meta_lock_check.py
```

Reports: revoked Meta `social_accounts` rows, the latest Meta security
email (subject/date via IMAP), heal-bot log tail, sidecar health, and
whether a login attempt is currently pending (which is what makes the
phone prompt appear).

## What the user must do (no automation exists)

1. **Keep at least one trusted device logged in** — phone AND desktop app
   both count. Do NOT log out; the lock screen only shows on fresh
   logins, and a surviving session is the recovery path.
2. Open the FB app → **Menu ≡ → Settings & privacy → Settings →
   Accounts Centre → Password and security**:
   - **"Where you're logged in"** — approve any datacenter/sidecar
     attempt listed there.
   - **"Login alerts"** — enable in-app + email so every future login
     attempt pings instantly.
3. **The "Was this you?" prompt is a push notification that only fires
   when a fresh login attempt happens.** If the user sees no prompt,
   there is no pending attempt — trigger one (below) or wait for the
   heal cron.
4. If a lock screen appears ("Review your login info to unlock"), tap
   **Get started** → confirm recent logins → set a new password. That
   fully unlocks; Meta may request ID verification.

## Triggering the approval prompt on demand

```bash
python3 .devin/skills/instagram-ops/scripts/ig_fb_session_heal.py --once
```

Drives the facebook-browser-sidecar through profile-picker → Continue →
checkpoint → `two_factor_required`. Watch `~/logs/ig-fb-session-heal.log`
for `two_factor_required` — that is the moment the push fires on the
user's devices. Caveats:

- **Churn guard**: the script defers if the sidecar restarted <600s ago
  (`session down; next login attempt in Ns`). Cron runs every 30min.
- **Code vs tap**: `two_factor_required` may want an SMS code, not just a
  tap. `phone_fb_approve.py` (2min cron) + `fb-approval-watch.sh` (5min)
  read the code off the phone via ADB — flaky when ADB/USB drops.
- If the browser page dies mid-flow (`Target page, context or browser has
  been closed`), just re-run; the checkpoint state persists server-side.

## After unlock — what heals itself

Once the user completes the unlock/approval, these lanes restore tokens
without further human input:

| Lane | Cadence | What it does |
|---|---|---|
| `ig_fb_session_heal.py` | 30min cron | sidecar login → session dump → account status active |
| `fb_session_heal` | 30min cron | FB page token refresh |
| `phone_fb_approve.py` | 2min cron | ADB reads SMS 2FA code off the phone |
| `fb-approval-watch.sh` | 5min cron | win_agent+OCR clicks "This was me" |
| `facebook_token_check` | 6h beat | debug_token sweep → alerts on revoked |
| `token_refresh` | beat | refresh-token grant where supported |

Verify recovery: `social_accounts.status` returns to `active` for
facebook/instagram/threads/whatsapp; then requeue failed
`post_targets`/`publish_queue` rows.

## What NOT to do

- Don't hammer the heal script — each attempt can trigger a new Meta
  checkpoint and restart the cooldown.
- Don't mark accounts `active` manually in SQL — the token is genuinely
  dead until Meta unlocks it; fake-active rows just fail publishes.
- Don't delete the user's logged-in mobile/desktop sessions — they are
  the recovery path.
- The unlock email's link is just `facebook.com/login` — there is no
  tokenized one-click unlock; completion requires the on-screen flow.
