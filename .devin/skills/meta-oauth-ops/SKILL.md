---
name: meta-oauth-ops
description: Re-authenticate Meta-platform SocialAuto accounts (facebook, instagram, threads, whatsapp) when tokens die — mint OAuth authorize URLs, drive them through the FB browser sidecar or CDP debug chrome, handle every 2FA lane (device notification, backup code, passkey limits), and verify social_accounts rows. Use for "instagram revoked", "threads re-auth", "FB OAuth", "IG onboarding wizard", "Meta 2FA", or the facebook_token_check alerts.
---

# Meta OAuth ops — reconnecting facebook / instagram / threads / whatsapp

Source of truth for how each `social_accounts` row heals. Verified live
2026-11-13/14 after a Meta password change revoked all four rows.

## 1. Where each platform connects

| Row | Lane | Notes |
|---|---|---|
| `facebook` user+page | `/api/v1/auth/oauth/facebook/authorize` → facebook.com/dialog/oauth | ONE flow heals personal profile AND every managed Page |
| `instagram cloudless.gr` | `instagram-onboarding` authorize → facebook dialog with `extras.setup.channel=IG_API_ONBOARDING` | `login_type=business_login`; plain `/instagram/authorize` 400s (INSTAGRAM_CLIENT_ID intentionally empty). Callback lands on the **facebook** callback URL |
| `threads cloudless.gr` | `/threads/authorize` → threads.net/oauth | needs a logged-in IG session in the driving browser |
| `whatsapp` | WABA embedded signup | separate lane, manual |

Mint URLs with `scripts/mint_authorize.sh <platform> [container]` (mints an
admin JWT inside the container — no stored secrets needed).

## 2. The proven automated path (instagram_reauth.py)

`backend/scripts/instagram_reauth.py` does the whole IG flow unattended:

```bash
docker exec -e SOCIAL_API_INTERNAL=http://localhost:8000 \
  social-api-green python /app/scripts/instagram_reauth.py
```

- **Container name**: the API may run as `social-api-green` behind
  `social-api-gateway` (blue-green deploy) — `social-api` may not exist.
- **SOCIAL_API_INTERNAL=http://localhost:8000 is mandatory**: uvicorn binds
  IPv6 loopback inside the container; `127.0.0.1:8000` is REFUSED.
- Throttle: `/tmp/instagram-reauth-state.json` (1 attempt/h, 3 fails → 1/day).
  `rm` it to force a run.
- Prereq: the **facebook-browser-sidecar (:9226) FB session must be logged
  in**. If it reports `login_wall`, heal it first with
  `scripts/sidecar_fb_login.py` (this skill).
- Exit codes: 0 ok / 2 lane-down / 3 human-gate / 4 flow-failed.

## 3. Sidecar FB login — `scripts/sidecar_fb_login.py`

Drives `facebook-browser-sidecar:9226` `/debug/navigate` + `/debug/eval` +
`/debug/page-text` through the full chain:

profile picker → "Use another profile" → email+pass → Meta-account password
step → 2FA method picker → **"Notification on another device"** → approval
lands in the Windows 11 Facebook app (user taps approve, sidecar logs in).

```bash
docker exec -e FB_LOGIN_EMAIL=… -e FB_LOGIN_PASSWORD=… \
  social-api-green python /app/scripts/sidecar_fb_login.py
```

2FA method picker items are `label` elements — click the label text, then the
LAST "Continue" div. Options observed: Passkey / Notification on another
device / WhatsApp +**38 / Backup code.

- **Passkey hangs headless** — WebAuthn waits for a platform authenticator;
  the "scan QR with Galaxy S22+" fallback never renders in a sidecar.
- WhatsApp/SMS go to +**38 (office SIM — NOT in the Galaxy S22+; the code
  will not arrive on the phone you can ADB into).
- Backup codes live in the IG/FB app: Accounts Center → Password and
  security → 2FA → Backup codes. Enter via "Try another way → Backup code"
  and **tick "Trust this device" first** (label containing that text) or the
  device re-challenges every login.

## 4. The headless-CDP lane (:9330) — `scripts/meta_oauth_drive.py`

`C:\Users\tbaltzakis\chrome-debug-profile` headless chrome, CDP :9330.
Use for flows needing a real browser (Threads consent, IG session checks).

- `Page(url_match=...)` attaches by URL **substring** (not regex) — use
  `dialog/oauth`, `force_authentication`, `two_step_verification`.
- Tabs wedge randomly (ws recv timeout) — probe each target, close dead
  ones via `GET /json/close/<id>`; healthy tabs keep working.
- New tab: `PUT /json/new?<url>`.
- IG session heal: profile picker → backup code + trust-device →
  `/accounts/onetap/` → "Not now" → feed.
- **IG onboarding wizard quirk**: "Get started" does `window.open` —
  headless spawns the popup once, then blocks it. Hook `window.open` FIRST
  (`window.open=function(u){window.__openurl=u;return{close(){},focus(){}}}`),
  capture the `instagram.com/accounts/login/?force_authentication=1&next=
  /oauth/oidc/…facebook_business_extension/oauth` URL, open it via
  `/json/new`, complete IG 2FA in that tab → it redirects to
  `facebook.com/facebook_business_extension/oauth/` (renders blank in
  headless but completes the FBE link server-side).
- `force_authentication=1` ALWAYS re-prompts 2FA — keep ≥2 unused backup
  codes in reserve and regenerate the set after each successful login.

## 5. TikTok gotcha

TikTok OAuth may mint rows for the wrong identity (`"Cloudless | Managed
Cloud"` — the app/business display name) while `tiktok cloudless.gr` stays
expired. During consent pick the **correct TikTok profile**, then verify
`select platform,username,status from social_accounts`.

## 6. Verify after every flow

```sql
select platform,username,status,account_type,token_expires_at
from social_accounts
where platform in ('facebook','instagram','threads','whatsapp')
order by platform;
```

Expected after full heal: facebook user+page `active` (~60d tokens),
instagram `active`, threads `active`. whatsapp remains a separate manual
WABA lane.

## 7. Boundaries

- This skill heals `social_accounts` rows. The legacy `FB_USER_TOKEN`
  operator gate is a different lane — `operator-gate-ops` skill.
- Never print/persist OAuth codes, tokens, passwords, 2FA or backup codes.
  Credentials via env only.
