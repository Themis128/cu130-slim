# meta-rotation-ops

End-to-end runbook for a **Meta Account password rotation** — the event that
kills every Meta-family credential at once (facebook, instagram, threads,
whatsapp tokens + all browser sessions). Verified live 2026-11-22: full
recovery with zero human steps.

## When to use

- `refresh_expiring_tokens` / `facebook_token_check` report
  `session has been invalidated because the user changed their password`
  across several Meta rows at once (see `meta-account-lock-ops` — that is
  invalidation, NOT the "Unlock your Meta Account" lock).
- After `meta_reset_watch.py` / `ig_pw_reset.py` / `fb_pw_reset.py` completes
  a reset — the new password MUST be propagated or every credential lane
  keeps using the dead one.
- After any manual Meta password change.

## The cascade (order matters)

1. **Determine WHICH password is live.** A reset flow can submit several
   candidates; only the last ACCEPTED one counts. Ground truth = Gmail:
   `Did you just change your password?` / `Your Instagram password has been
   changed` — the newest such mail timestamp ≈ the accepted submission.
   The post-change SMS "Confirm your Meta Account" step is login-code
   ENROLLMENT, not a gate on the change — the password is already live.

2. **Write it to BOTH .env files** — repo root `.env` AND
   `deploy/socialauto-standalone/.env` (the `env_file` that actually feeds
   containers). Set `INSTAGRAM_PASSWORD` AND `FACEBOOK_PASSWORD` — one
   shared Meta Account secret. env_file injects on container CREATE only;
   `docker compose up -d` recreates, plain restart does NOT reload.
   Queue-safe: another agent may have just recreated the stack — check
   `docker inspect <c> --format '{{.Created}}'` before forcing a recreate.

3. **Heal the canonical IG lane** — `business_login` publishes via
   `graph.instagram.com` and needs NO Page link (immune to #10/PPA):

   ```bash
   # websockets is REQUIRED — bare python3 fails silently (fixed 2026-11-22
   # to fail fast, but use the venv anyway):
   python3 -m venv ~/.venv-igdrive && \
     ~/.venv-igdrive/bin/pip install websockets && \
     ~/.venv-igdrive/bin/python \
     .devin/skills/ig-business-login-ops/scripts/ig2_oauth_drive.py
   ```

   - `--verify` first: exits 0 if the stored token already answers
     `graph.instagram.com/me` (the weekly `refresh_instagram_tokens` beat
     can self-heal once Meta's lock lifts — check before driving).
   - Drive handles: profile chooser → login form (INSTAGRAM_PASSWORD) →
     2FA method picker → TOTP (`META_TOTP_SECRET_IG`) → consent Allow.
   - Needs CDP debug Chrome on :9229 (`192.168.96.1:9229` from WSL).

4. **Verify the lane, not the row** — DB `status='active'` lies:

   ```bash
   docker exec social-api python3 /app/scripts/ig_lane_doctor.py report
   docker exec social-api python3 /app/scripts/ig_lane_doctor.py publish-probe 38ddbd44
   docker exec social-api python3 /app/scripts/account_truth.py
   ```

   `publish-probe` creates a throwaway media container (never publishes).
   Probe image must be a LIVE public URL — a 404 asset fails with media-type
   2207052 and looks like a lane failure (fixed: R2 JPEG).

5. **Repoint stranded targets** — pending `post_targets` bound to dead
   lanes:

   ```bash
   docker exec social-api python3 /app/scripts/ig_lane_doctor.py pending
   docker exec social-api python3 /app/scripts/ig_lane_doctor.py repoint <acct-prefix>
   # or .devin/skills/ig-business-login-ops/scripts/repoint_publish_target.py
   ```

   Duplicate hazard: a post can hold targets on BOTH IG rows — check
   `post_targets.platform_url` for a live URL before requeueing a failed
   twin or the same post ships twice.

6. **FB-family rows** — `facebook` user+page heal via ONE OAuth flow
   (`/api/v1/auth/oauth/facebook/authorize`); sidecar FB login via
   `meta-oauth-ops`/`sidecar_fb_login.py`. Threads rows stay `revoked`
   until their own re-auth — expected.

## SMS/email code lanes (when 2FA demands a code)

- **SMS**: `adb shell content query --uri content://sms/inbox` on the
  Galaxy S22+ (`100.84.31.85:5555` / `192.168.1.77:5555`). Meta codes read
  "Use NNNNNN for two-factor authentication".
- **Gmail**: `gmail-token.json` + `gcp-oauth.keys.json` under
  `/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/` — HTML-only
  Meta mails need `format=full` + `text/html` part decode.
- **STALE-CODE RACE (verified 2026-11-22)**: Meta invalidates prior SMS
  codes on every resend AND concurrent challenges (IG login 2FA + Meta
  account confirm) send codes to the SAME phone — a code from challenge A
  fails on challenge B. Record the resend timestamp, accept ONLY codes
  newer than it, match sender wording ("Meta account" vs "Instagram"),
  submit ONCE. Rejected code ⇒ request a fresh resend, never reuse.
- **WhatsApp 2FA is a dead lane** on this phone (WA stuck at "Verifying
  your number") — always pick Text message / Authentication app instead.
- **IG browser session without password (verified 2026-11-22)**:
  `ig_emaillogin_session.py` fetches Meta's `web_emaillogin` link → 2FA
  picker → "Authentication app" TOTP → trust-device → sessionid. After
  the Meta security event, `business_login` tokens die with
  `190 session invalidated` — re-run `ig2_oauth_drive.py` to re-mint,
  then `ig_lane_doctor.py report` must show `token-valid` +
  `publish lane: OK`. Persist the live cookies into
  `social_accounts.meta_data.browser_cookies` (platform='instagram') so
  `session_healer`/CDP lanes see them.
- **Reset-confirm tokens are single-use** — a failed submit ("Something
  went wrong") burns the link; the page then reads "Expired token".
  Don't retry submits; fetch a fresh `web_emaillogin`/reset mail.
- **Profile picker after login** — instagram.com may land on
  "Use another profile"/login wall; navigate to `instagram.com/` again
  and the feed loads for the last-used profile. Verify with the
  `ds_user_id` cookie, not `document.cookie` alone.

## Known platform gates (NOT automatable — document, don't churn)

- **fb_onboarding publish #10** — Page↔IG link exists but PPA incomplete;
  known Meta Graph bug. `business_login` is canonical; never publish via
  fb_onboarding until `publish-probe` passes.
- **Page↔IG link upgrade** — blocked by permanent Feb-2021 Account Quality
  ad-policy flag on the personal FB account ("can't be reviewed — too
  much time has passed"). Escalation lane: `meta-support-ops` skill.
- **WABA / WhatsApp Business** — dies with the Meta session; re-verify
  phone AFTER unlock (lock suppresses verification SMS).

## Tools

| Tool | Purpose |
|---|---|
| `ig-business-login-ops/scripts/ig2_oauth_drive.py` | cold-start IG Business Login OAuth (needs websockets venv) |
| `ig-business-login-ops/scripts/ig_bl_session_drive.py` | same flow via stored session blob (no password) |
| `ig-business-login-ops/scripts/ig_emaillogin_session.py` | passwordless web_emaillogin link lane |
| `credential-reset-ops/scripts/meta_reset_watch.py` | long-running auth.meta.com reset watcher (Gmail codes) |
| `credential-reset-ops/scripts/ig_pw_reset.py` / `fb_pw_reset.py` | per-platform forgot-password drives |
| `backend/scripts/ig_lane_doctor.py` | lane report / pending / repoint / publish-probe |
| `backend/scripts/account_truth.py` | DB-status vs live-probe drift |
| `meta-oauth-ops/scripts/sidecar_fb_login.py` | FB sidecar re-login drive |
