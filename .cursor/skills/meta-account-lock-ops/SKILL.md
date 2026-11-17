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

## Ad-account / advertising restrictions — a DIFFERENT failure class (verified 2026-11-16)

Separate from login locks: Meta can restrict the **FB account / ad
accounts / business portfolio** for advertising while logins work fine.
These surface as unrelated feature blocks — e.g. the Page↔Instagram link
flow ends with *"resolve some issues with your Facebook account … View
details in Account Quality"*.

Map (drive on the debug Chrome / sidecar):

| URL | Shows |
|---|---|
| `business.facebook.com/business-support-home/` | 30-day issue window — misses OLD flags |
| `…/business-support-home/<business_id>/` | **accounts list**: FB-account restriction, ad accounts, catalogs, Pages, data sources |
| `…/business-support-home/<business_id>/<ad_account_id>/` | per-asset detail + "What you can do" |

- `facebook.com/accountquality` forces a switch to the personal profile —
  Page context can't view it.
- Three levels are DISTINCT: FB-account-level (the person), ad-account,
  business-portfolio. A clean portfolio does NOT imply a clean person.
- "Can't be reviewed — too much time has passed" = the flag is permanent;
  no self-serve review. Escalation lanes on the detail page: "Chat with
  Meta AI" (24/7 assistant — the appeal history lives in its chat list),
  "Go to Business Settings" (add a clean ad account), business
  onboarding "Get started".
- Current state (2026-11-16): personal FB account restricted Feb 2021 +
  personal ad account `657781691826702` disabled Jan 2021 + ad account
  `2771400643144250` restricted — all from Advertising Standards, all
  unreviewable. Blocks the cloudless.gr Page↔IG connection upgrade.
  Business portfolio `1558125105019725` is clean.

## What NOT to do

- Don't hammer the heal script — each attempt can trigger a new Meta
  checkpoint and restart the cooldown.
- Don't mark accounts `active` manually in SQL — the token is genuinely
  dead until Meta unlocks it; fake-active rows just fail publishes.
- Don't delete the user's logged-in mobile/desktop sessions — they are
  the recovery path.
- The unlock email's link is just `facebook.com/login` — there is no
  tokenized one-click unlock; completion requires the on-screen flow.

## Phone-side unlock (proven 2026-11-16)

The "log in with another device to unlock your Meta Account" gate unlocks
cleanly on the **Galaxy S22+** — it IS a previously-used device (FB app
installed since Sept, Messenger + Pages too):

1. `monkey -p com.facebook.katana` — app shows the email-code screen
   ("Check baltzakis.themis@gmail.com").
2. The code arrives in **the phone's own Gmail app** — visible as an inbox
   snippet "Use <code> to get back into your..." and in the notification
   extras (`dumpsys notification`, pkg `com.google.android.gm`). Read it
   with `human-gate-bridges/scripts/phone_gmail_otp.py --sender meta`
   (no root, no OCR — inbox rows are uiautomator text nodes).
3. Enter code → "Create a new password" is **mandatory** (old passwords
   die). The stored `.env` `FACEBOOK_PASSWORD` was accepted as the new
   value — reusing it keeps every heal lane credential-synchronized.
4. "Session expired" → log back in with the same creds. Feed loads;
   account unlocked server-side.

Corollaries proven this incident:

- The lock **suppresses unrelated Meta-surface sends** — WhatsApp
  Business verification SMS to +30 697 777 7838 never arrived while the
  account was locked (Instagram/LinkedIn SMS flowed normally). Re-request
  AFTER unlock, not during.
- The WABA/Graph token dies with the lock (190/460 session-invalidated) —
  re-auth via SocialAuto console after unlock, don't trust DB status
  until `account_truth.py` confirms.

## Meta-wide session invalidation (no lock email — verified 2026-11-16)

A different event class from the lock: Meta invalidates **existing**
sessions/tokens server-side (password change, security session reset) —
no "Unlock your Meta Account" mail, no lock screen. Signature observed
simultaneously:

- `refresh_expiring_tokens` sweep errors: `instagram/<acct>: Error
  validating access token: The session has been invalidated because the
  user changed their password or Facebook has changed the session for
  security reasons.` — hits several Meta rows at once, not one expiring.
- `facebook-browser-sidecar` stuck at the **profile picker** (FB's
  "Continue as X / Use another profile" re-auth screen): log shows
  `handleProfilePicker error: locator.click: Timeout 30000ms exceeded` +
  `<div ...> from <div ...> subtree intercepts pointer events` looping
  every ~30s — the picker button is under an overlay Playwright can't
  click through. A browser `storage_state` that was working minutes
  earlier is dead.
- Side effect: publishes targeting the sidecar stall in `publishing`
  until the client's HTTP timeout fires (post_video = 300s) — the post
  then retries/fails; no data loss. See `publish-ops` stuck-publish
  triage. Do NOT mass-revoke the task or re-render media — the assets
  are fine; only auth is dead.

Response ladder:

1. Confirm it's invalidation, not a wedge: `scripts/meta_lock_check.py`
   + the two signatures above. If only ONE row died it's expiry, not an
   event — use `token-lifecycle-ops` instead.
2. Sidecar re-login is interactive (credentials + 2FA tap/SMS on the
   user's phone — the documented FB sidecar login lane in host
   `global_rules.md`: picker → "Use another profile" → email+pass → 2FA
   via office SIM/WhatsApp, approval lands on the Windows-11 FB app or
   the Galaxy S22+). Drive it or hand the user the exact steps; the
   picker-click wedge does NOT self-heal.
3. Token lanes (`fb_reauth.py`, `meta-oauth-ops`) mint fresh Graph
   tokens once the user credential is live again.
4. Requeue the publishes that failed during the window — media assets
   persist; `publish_retry`/`media_heal` pick them up once the account
   row is `active` again (`account_truth.py` verifies).
