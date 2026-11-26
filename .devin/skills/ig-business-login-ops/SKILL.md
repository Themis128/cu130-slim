# ig-business-login-ops

Revive and operate the **Instagram Business Login** (`instagram2`) lane —
`graph.instagram.com` + `instagram_business_*` scopes — which publishes
**without** the Facebook Page `instagram_business_account` link.

## When to use

- IG publishes fail with `(#10) Requires instagram_content_publish permission`
  or `Application does not have permission` on `graph.facebook.com/.../media`
- `ig_link_probe.py` reports `instagram_business_account=False` on the Page
- The `fb_onboarding` IG row is `active` (reads work) but publish is dead
- After Meta password changes / integrity events that sever Page↔IG links

## The two IG lanes (do not confuse them)

| Lane | Host | Needs | Row meta_data.login_type |
|---|---|---|---|
| Facebook Login for Business | `graph.facebook.com` | Page `instagram_business_account` link | `fb_onboarding` |
| **Instagram Business Login** | `graph.instagram.com` | IG professional account + IG App ID | `business_login` |

The fb_onboarding lane broke 2026-11-21/22 when the Page lost
`instagram_business_account` — a **known unresolved Meta Graph bug**
(developers.facebook.com/community/threads/3087865364750080,
gitroomhq/postiz-app#2219). Re-auth does NOT fix it. The business_login
lane is immune — prefer it as canonical for publishing.

## Recovery runbook (verified live 2026-11-22)

1. **Check lane state** — `ig_link_probe.py` + list instagram rows:
   the `business_login` row (`38ddbd44-…` for cloudless.gr, IG user id
   `28747382798219804`) may be `expired` while `fb_onboarding`
   (`d2215106-…`, id `17841436821573754`) stays `active`.
2. **Ensure env** — `INSTAGRAM2_CLIENT_ID/SECRET/REDIRECT_URI` must be in
   `deploy/socialauto-standalone/.env` (NOT just repo-root `.env`) and the
   container recreated. INSTAGRAM2_CLIENT_ID is the **Instagram App ID**
   (`1462851805249610`), not the Meta app id — wrong id → "Page isn't
   available" on instagram.com/oauth/authorize.
3. **Revive session → OAuth** — `scripts/ig_bl_session_drive.py`:
   decrypts `sessionid`/`ds_user_id` from the fb_onboarding row's
   `meta_data.private_api_session_*` (via `decrypt_field` inside
   social-api), injects them into CDP Chrome (`:9229`) with
   `Network.setCookie`, opens the authorize URL → consent → callback 200 →
   business_login row flips `active`.
   - No password/2FA needed while the private-API session blob is alive.
   - If no session blob: bootstrap one passwordlessly —
     `scripts/ig_emaillogin_session.py --dump /tmp/ig_cookies.json --inject`
     fetches Meta's "Log back into Instagram" mail, passes the 2FA picker
     via META_TOTP_SECRET_IG (stdlib TOTP — the secret is 80-bit, under
     pyotp's floor), and injects cookies into CDP Chrome. Then re-run.
4. **Verify** — script probes `graph.instagram.com/v21.0/me` and prints
   `VERIFY active cloudless.gr <user_id>`.
5. **Repoint stuck targets** — `scripts/repoint_publish_target.py`:
   `repoint --post <uuid> --from <fb_onboarding_id> --to <business_login_id>`
   moves queue+target rows; worker publishes on next beat.
   `add --post <uuid> --account <uuid>` adds an extra target (e.g. personal
   FB profile).


## force_authentication wall + full credential re-login lane (verified 2026-11-23)

`instagram.com/oauth/authorize` can bounce to
`/accounts/login/?force_authentication&next=<oauth>` even with a live
sessionid — the OAuth flow demands a FRESH credential login. The
emaillogin session alone is not enough.

End-to-end lane (all automated, `scripts/ig_oauth_2fa_drive.py`):

1. `ig_emaillogin_session.py --dump /tmp/ig_cookies.json --inject` —
   passwordless session into CDP Chrome :9229 (needs playwright venv:
   `~/.venv-igreset`).
2. Open the instagram2 authorize URL — if it lands on
   `force_authentication`, fill `INSTAGRAM_USERNAME`/`INSTAGRAM_PASSWORD`
   from social-api env, submit.
3. 2FA: IG defaults to **WhatsApp** code. "Try another way" opens a Bloks
   picker — select **Authentication app** or **Backup code**. CRITICAL:
   only `label.click()` on the option's `<label>` flips the radio;
   div clicks and `input.click()` do nothing (React-controlled Bloks).
   The picker renders duplicate hidden option sets — use the LAST visible
   label. The modal's Continue is the LOWEST visible 'Continue' on screen.
4. Codes: TOTP via `META_TOTP_SECRET_IG` (container env; may be STALE
   after Meta security resets — it was rejected 2026-11-23). Reliable
   fallback: `META_BACKUP_CODES` in `social_secrets` (JSON array of
   one-time 8-digit codes — consume one, write the rest back; the driver
   does this via `consume_backup_code`).
5. `/consent/?flow=ig_biz_login_oauth` → click **Allow** → tab hits
   `/api/v1/auth/oauth/instagram2/callback?code=…` → social-api exchanges
   → `business_login` row flips `active` → `ig_lane_doctor.py report`
   shows `token-valid` / `publish lane: OK`.

Gotchas:
- CDP :9229 down → `Start-ScheduledTask ChromeDebug` on Windows (hidden
  via winps). Fresh instance may lack `--remote-allow-origins` → all
  websocket clients need `suppress_origin=True` (patched into
  `ig_bl_session_drive.py`).
- WhatsApp 2FA code does NOT surface as a phone notification reliably —
  prefer backup-code/TOTP lanes over waiting for it.
- Backup codes are single-use: the driver removes the consumed code from
  `social_secrets` so the store stays truthful.

## Phone-Chrome recovery lane (verified live 2026-11-24 — minted 38ddbd44)

When the IG-native password is stale/rejected AND desktop CDP lanes are
down, the whole re-mint runs on the **phone's Chrome** via ADB CDP —
`scripts/ig_phone_reauth.py` encodes the proven sequence:

1. `instagram.com/accounts/password/reset/?find_by=username` →
   `cloudless.gr` → Continue → **Meta Account gate** (FB-SSO, not IG).
2. "Try another way" → "Get code or link via email" → gmail option →
   8-digit code arrives as a **Gmail push notification** — read it off
   `adb shell dumpsys notification --noredact` (no mailbox creds).
3. Next gate = WhatsApp 2FA → "Try another way" → **Backup code** radio
   (LAST radio row) → codes from `~/ig_backup_codes.txt` — pull a FRESH
   list from IG app → Accounts Center → 2FA → Backup codes first; old
   lists go stale when Meta rotates them.
4. "Create a new password" gate → re-enter `INSTAGRAM_PASSWORD` (same
   value = no effective rotation; tokens survive).
5. Logged-in instagram.com session → navigate to the instagram2
   authorize URL → consent "Allow" — on wbloks consent a JS
   `el.click()` works where synthetic mouse events don't →
   `instagram2/callback?code=…` → row flips `active`.

Prereqs: `ADB_SERIAL` (tailnet IP `100.84.31.85:5555`), phone unlocked,
Chrome foregrounded (`phone_cdp.front()`), `websocket-client`.
Transport: `phone-web-auth-ops/scripts/phone_cdp.py` (forward tcp:9555).
Resume mid-flow with `--step N`; skip URL minting with `--authorize-url`.

Battle-tested 2026-11-24 (second run): live-session short-circuit →
authorize URL → consent Allow → callback → row `38ddbd44` refreshed,
fresh token verified on `graph.instagram.com/v26.0/me`. Lessons baked
into the script: Chrome suspends background tabs (page ws accepts but
eval hangs — every candidate is probed before use), `find_by=username`
does NOT preselect the identifier field (the "Find by email or username
instead" toggle needs a substring click), and the consent URL embeds the
callback inside `params_json` so `social.cloudless.gr` alone
false-positives — the real landing carries `?code=…&state=…`.

Side benefit: enrolling **Authentication app** TOTP in IG app →
Accounts Center → 2FA kills the dead-SIM dependency — secret converged
to `META_TOTP_SECRET{,_AC,_IG}` = `ZUSD…` on 2026-11-24.

### `force_authentication` wall on the authorize URL (verified 2026-11-26)

Even with a LIVE IG session, `instagram.com/oauth/authorize` can demand
a fresh credential submit. `scripts/ig_phone_credential_login.py`
handles it: finds the IG tab → fills `input[name=username]` /
`input[name=password]` via click + `Input.insertText` (Bloks ignores
synthetic value-sets) → real mouse click on "Log in" → polls past the
wall. Exit 0 = advanced, 4 = still walled (bad creds / extra gate).

For the consent+callback tail once the session is live (any platform),
`phone-web-auth-ops/scripts/phone_oauth_drive.py --mint instagram2|threads`
does navigate → consent-gate clicks → callback detect generically.

## Gotchas

- **Two active instagram rows for the same account is a hazard** — the
  daily pipeline may target both → duplicate posts or #10 noise on the
  fb_onboarding row. Canonical = `business_login` for publish; consider
  demoting fb_onboarding in account pickers (TODO in BACKLOG).
- `instagram.com/oauth/authorize` returns "Page isn't available" when the
  browser has **no IG session** — it does not bounce to login. Always
  inject cookies first.
- Codes are single-use: if the API callback consumes the `?code=`, manual
  exchange fails with "authorization code has been used" — that means the
  lane already succeeded; just verify the row.
- Long-lived token (~60d) refreshes via `ig_refresh_token` grant —
  `token_refresh.py` already handles `business_login` rows.
- Orphan media containers (`/media` without `/media_publish`) expire
  harmlessly.

## Accounts (cloudless.gr)

- business_login row: `38ddbd44-8811-4d0b-be62-a23fd2f50490`,
  account_id `28747382798219804`, IG user_id `17841436821573754`
- fb_onboarding row: `d2215106-7e54-4c43-a5f4-383b1ae0c61e`,
  account_id `17841436821573754`
- Page: `116436681562585` (cloudless.gr)
