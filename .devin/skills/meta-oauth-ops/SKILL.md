---
name: meta-oauth-ops
description: Re-authenticate Meta-platform SocialAuto accounts (facebook, instagram, threads, whatsapp) when tokens die — mint OAuth authorize URLs, drive them through the FB browser sidecar or CDP debug chrome, handle every 2FA lane (device notification, backup code, passkey limits), and verify social_accounts rows. Use for "instagram revoked", "threads re-auth", "FB OAuth", "IG onboarding wizard", "Meta 2FA", or the facebook_token_check alerts.
---

# Meta OAuth ops — reconnecting facebook / instagram / threads / whatsapp

> **HARD RULE — NEVER CHANGE META CREDENTIALS** (owner directive
> 2026-11-24): the Meta Account password is owner-set — never rotate,
> regenerate, reset, or "fix" it. Recover dead tokens/sessions via OAuth
> / backup codes / emaillogin only — never via password change.

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

### Fast path (default since 2026-11-15, PR #1949)

`instagram_reauth.py` strips the `extras={"setup":{"channel":"IG_API_ONBOARDING"}}`
param from the minted authorize URL before navigating. Meta's embedded
"Connect to Instagram" wizard is a known-broken flow (Meta developer forum /
SO) that strands automation at "Get started". Without extras the dialog is a
plain FB consent; the signed state still carries `p=instagram-onboarding`, so
the facebook callback runs identical IG discovery
(`me/accounts` -> `instagram_business_account` / `page_backed_instagram_accounts`).

- Verified live 2026-11-15: "Continue as" -> callback -> new `social_accounts`
  instagram row `active` in ~15 s.
- `IG_WIZARD=1` env restores the wizard path — needed only when the target IG
  is NOT yet linked to a FB page the user admins (discovery then finds
  nothing and the callback 400s).
- Caveat: fast path binds whichever IG the page-backing resolves to — verify
  `account_id`/`ig_id` is the intended brand account afterwards.

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

**TOTP unattended 2FA (2026-11-27)** — `meta_oauth_drive.py` now self-serves
the `GATE_2FA` state: tries "Use a different method/Try another way" →
"Authentication app" → fills a code input
(`approvals_code`/`one-time-code`/`inputmode=numeric`) with an inline
RFC-6238 TOTP (no pyotp dep) → submits. Secret source env, first hit wins:
`META_TOTP_SECRET` → `META_TOTP_SECRET_AC` → `META_TOTP_SECRET_IG` — all
three already exist in the social-api container env (Accounts Center TOTP is
enrolled; FB+IG+Threads share it while linked). With `FB_EMAIL`+`FB_PASS`+
secret exported, the full chain mint→login→2FA→consent→callback runs
unattended. Falls back to the old human-wait poll when no secret is set.
`sidecar_fb_login.py` already had the same lane (`META_TOTP_SECRET` env).

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

## 7. IG↔FB Page link — the REAL surface (verified 2026-11-16)

The OAuth onboarding wizard is the WRONG lane for linking an existing IG
professional account to a Page. The correct, deterministic surface is:

```
FB (switched into the Page — "Switch Now" on the Page's banner)
→ Settings & privacy → Settings → Linked accounts → Instagram
→ https://www.facebook.com/settings/?tab=linked_profiles
→ Instagram row → https://www.facebook.com/settings/?tab=linked_instagram
```

States observed on `?tab=linked_instagram`:
- `Connected to @handle` + "Review account connection — only some
  features" = **limited link exists** (IG↔profile pairing). Graph
  `connected_instagram_account` may still be absent — the field only
  populates after the full Page↔IG connection completes.
- "Review connection" opens a **multi-step accordion inside ONE
  `[role=dialog]`**: Get-more-features `Confirm` → "Confirm Your Page
  and Instagram connection" `Confirm connection` → "Choose Instagram
  message settings" `Confirm` → spawns an
  `instagram.com/oauth/oidc/?app_id=…&redirect_uri=…facebook.com/page/instag…`
  login popup. Each click only reveals the next section — keep dumping
  the same dialog's innerText.

The OIDC popup (`enable_fb_login=0` = IG-native login):
- Page renders footer-only for ~10-20s before the real body appears —
  don't declare it blank early; check buttons before text.
- `Log in as @handle` authorizes with the existing IG session (no
  password needed) → popup self-closes on success (WS-DIED = progress).

### The gate that fails last (verified live)

After the IG login completes, FB checks the **acting admin's FB account
standing**. With any restriction it renders:

> "Unable to connect to Instagram — Before connecting this Facebook
> Page and Instagram account, you'll need to resolve some issues with
> your Facebook account. View details in Account Quality."

Account Quality surfaces:
- `https://business.facebook.com/business-support-home/` — overview,
  "Recent account issues" (30-day window only — misses old flags)
- `…/business-support-home/<business_id>/` — **accounts list**: FB
  account restriction, ad accounts, catalogs, Pages, data sources
- `…/business-support-home/<business_id>/<ad_account_id>/` — per-asset
  detail + "What you can do" (review button only when reviewable)
- Restriction levels are DISTINCT: FB-account-level (person),
  ad-account-level, business-portfolio-level. cloudless.gr's block is
  FB-account-level (Feb 2021 ad-policy flag) + disabled personal ad
  account (Jan 2021, "can't be reviewed — too much time passed").
- Escalation lanes on the detail page: "Chat with Meta AI" (24/7
  assistant, chat history lives there), "Go to Business Settings"
  (add a clean ad account), business onboarding "Get started".

**Graph truth**: `GET /<PAGE_ID>?fields=instagram_business_account,
connected_instagram_account` — both fields are OMITTED when empty (not
errors). `instagram_business_account` = the API-usable business link;
`connected_instagram_account` = the settings link. Probe via
`scripts/ig_link_probe.py`.

**IG-side alternative**: IG app → Edit profile → Page (mobile-only —
the web edit page literally says links/Page editing is app-only) —
likely hits the same FB-account gate because the acting admin's
standing is checked either way.

## 8. CDP driving mechanics for Meta UI

- `GET /json/new?url` requires **PUT** on newer Chrome (405 otherwise).
- **JS `.click()` can never open `window.open` popups** — use trusted
  `Input.dispatchMouseEvent` (mousePressed+mouseReleased at element
  center). Scroll `scrollIntoView({block:'center'})` first; viewport-
  relative coords go stale.
- **`offsetParent !== null` is a broken visibility test** for Meta's
  `position:fixed` dialog buttons — use `getClientRects().length > 0`.
- WS-DIED / StopIteration on `_ws_url` = the tab navigated or closed —
  re-resolve the tab id by URL substring from `/json/list`, or the
  popup self-closed (usually progress, not failure).
- Meta settings tabs wedge randomly — close via
  `GET /json/close/<id>`, reopen via `PUT /json/new`.
- `forced_account_switch` interstitial = a plain FORM with one
  `input[type=submit] Continue` — trusted-click it.
- React inputs ignore programmatic `innerText`/`.value` — use the
  native setter + `input` event (contenteditable chat boxes still
  mostly ignore it — Meta AI assistant is one such case).
- Page-context switch: "Switch Now" on the Page banner sets a session
  cookie browser-wide — after it, `/settings` is the PAGE's settings
  (shows "Page details"/"Page setup"); before it, personal settings.

## 9. Boundaries

- This skill heals `social_accounts` rows. The legacy `FB_USER_TOKEN`
  operator gate is a different lane — `operator-gate-ops` skill.
- Never print/persist OAuth codes, tokens, passwords, 2FA or backup codes.
  Credentials via env only.
