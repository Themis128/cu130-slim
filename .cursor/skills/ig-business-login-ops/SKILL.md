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
   - If no session blob: heal IG session first (instagram-ops skill),
     then re-run.
4. **Verify** — script probes `graph.instagram.com/v21.0/me` and prints
   `VERIFY active cloudless.gr <user_id>`.
5. **Repoint stuck targets** — `scripts/repoint_publish_target.py`:
   `repoint --post <uuid> --from <fb_onboarding_id> --to <business_login_id>`
   moves queue+target rows; worker publishes on next beat.
   `add --post <uuid> --account <uuid>` adds an extra target (e.g. personal
   FB profile).

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

## Cold-start fallback — `scripts/ig2_oauth_drive.py` (no session blob)

When the stored `sessionid` is dead (password rotation invalidates every
IG session + derived token — Graph 190 subcode 460), the cookie-inject
lane can't start. `ig2_oauth_drive.py` does the full login instead:

1. Mints the signed authorize URL inside `social-api` (signed state +
   `instagram_business_*` scope string).
2. Navigates the **main** `instagram.com` tab (exact URL match — a
   substring pick can land on a stale login/reset tab and drive the
   wrong page; the driver pins `flow_id` and follows that target only).
3. Handles every screen: profile chooser (`cloudless.gr` tile — no
   username input exists there), username+password form
   (`INSTAGRAM_PASSWORD` from `.env`), 2FA method picker (clicks the
   `Authentication app` *label* — radio indices shift), TOTP fill+submit
   (`META_TOTP_SECRET_IG` — click the Continue *closest below* the code
   input; the page has two), consent `Allow`.
4. `--verify` short-circuits when the stored token already answers
   `graph.instagram.com/me` — run that first, always.

Verified live 2026-11-22 after the password-rotation kill: full drive →
callback 200 → `VERIFY active cloudless.gr`.

**Password rotation cascade**: `ig_pw_reset`/`meta_reset_watch` write the
new secret to repo-root `.env` ONLY — `deploy/socialauto-standalone/.env`
(the file that actually feeds containers via `env_file`) keeps the old
one. After any rotation, sync `INSTAGRAM_PASSWORD` +
`META_TOTP_SECRET_IG` into the standalone `.env` and `docker compose up
-d` the workers — restarts do not reload `env_file`.

## Accounts (cloudless.gr)

- business_login row: `38ddbd44-8811-4d0b-be62-a23fd2f50490`,
  account_id `28747382798219804`, IG user_id `17841436821573754`
- fb_onboarding row: `d2215106-7e54-4c43-a5f4-383b1ae0c61e`,
  account_id `17841436821573754`
- Page: `116436681562585` (cloudless.gr)
