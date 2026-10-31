# meta-ads-ops

Diagnose + operate Meta ad-account, billing, and business-portfolio health
through the **facebook-browser-sidecar** (`localhost:9226`) — the logged-in
Meta session the social stack already keeps warm. Built from the 2026-10-29
forensics: every Meta product surface (Ads Manager, Billing hub, Account
Quality, WhatsApp onboarding, business-portfolio permissions) hangs off the
same underlying account/risk state, so inspect them together.

## Surfaces (all reachable from the sidecar session)

| Surface | URL | Tells you |
|---|---|---|
| Ads Manager accounts | `adsmanager.facebook.com/adsmanager/manage/accounts` | restriction banner, spend, opportunity score |
| Payment settings | `business.facebook.com/ads/manager/billing/payment_methods/?act=<ad_account>` | "Ad account disabled", business-location verify gate, balance, payment methods |
| Billing hub | `business.facebook.com/latest/billing_hub/payment_methods/?asset_id=<waba_asset>&business_id=<biz_id>` | portfolio vs messaging-account payment methods, finance-editor flow |
| Account quality | `facebook.com/accountquality` (redirects to business-support-home) | outstanding violations / support cases (last 30 days) |
| WhatsApp onboarding | `developers.facebook.com/apps/<app>/whatsapp-business/wa-settings/` | ON_PREMISE→Cloud "Continue" + the temporary-block state |

## Restriction taxonomy (verified 2026-10-29, web + Meta AI)

**Levels** — restrictions sit on the personal profile / business portfolio /
individual ad account; each has a different fix, and a flagged owning
profile cascades to everything below it.

**Status codes** (Restrictions Info in Account Quality):
- `2FAC` — needs 2FA on the personal profile; lifts **immediately** when set
- `DISABLED RISK` / `RISK_REVIEW` — suspicious payment/geo activity (e.g.
  the Kazakhstan-address-on-Greek-business flag); review-able
- `PREHARM` — identity verification failed; doc-upload loop, fix via
  Meta ADS app → Help Center → account quality → upload doc
- `REJECT_NORETRY` / `BUSINESS_INTEGRITY_RAR` — permanent, no recovery

**Hard rules:**
- Ad account disabled **>180 days cannot be reinstated** — create a new
  ad account inside a healthy portfolio instead (Meta's own advice)
- Missing "Request Review" = not eligible / already reviewed / permanent —
  another **verified admin** may raise it
- Only ONE appeal — repeat submissions delay, don't help
- Meta Support Pro exists for disabled accounts (ad account ID required)

**Our case (2026-10-29):** profile `1134463867` flagged + ad acct
`657781691826702` disabled Jan-2021 → appeal window expired. Remediation
chain: profile 2FA + identity confirmation (`/id/hub` → "Protect your
account") → portfolio business verification → NEW ad account → WhatsApp
migration (`wa_onboard_retry.py`).

## Restriction flags (what page text means)

- **"can't calculate a score when your account is restricted"** (Ads Mgr) —
  account carries SOME restriction; check Payment settings for the type.
- **"Ad account disabled … unusual activity"** — the hard one: Meta flagged
  the account. Usually paired with a **business-location verify gate** —
  fix the geo info first, then appeal via the "Take action" link.
- **"Verify your business is in <country>"** — geo mismatch; correct the
  business address (billing hub → Business info) then the flag lifts on review.
- **"not allowed to modify finance_permission"** — business-portfolio
  finance-editor rights are locked; the personal-ad-account billing path
  bypasses this when the account itself isn't disabled.
- **"temporarily blocked from performing this action"** (WhatsApp onboarding)
  — risk-engine hold; retry after cooldown or from a different fingerprint.

## Scripts

- `scripts/meta_ads_audit.py [quick|full]` — drives the sidecar through all
  surfaces, extracts restriction/verify/disabled flags, prints a status
  report. `quick` = ads manager + payment settings only.
- `scripts/meta_appeal.py` — drives the disabled-account appeal: finds the
  "Take action"/"Request review" button on Ads Manager, clicks it, dumps
  the resulting dialog/page so the next step is visible. Retries on the
  sleepy-sidecar timeouts; idempotent (safe to re-run).
- `scripts/meta_appeal_form.py` — opens Meta's canonical disabled-account
  appeal form (`help/contact/2026068680760273`) and inventories its
  fields/buttons so the fill is known before a human types a word.
- `scripts/meta_restriction_heal.py` — the remediation driver for the
  USER'S REAL CHROME via CDP :9222 (the sidecar can't do this surface):
  `status` / `shot` / `nav` / `step` / `login <email>` / `twofa` /
  `confirm <code>` / `totp`. Real Input-event clicks for the
  iframe-walled accountscenter pages; `FB_PASSWORD`/`FB_TOTP_SECRET`
  env vars for the credential+TOTP steps.

**Device-trust gate (learned 2026-10-29):** a fresh Chrome session hits
"you can't make this change at the moment" on sensitive changes — the
notification-approval from a trusted device does NOT instantly clear it;
the device must age in (normal use) OR do the change on the trusted
device (the phone's Facebook app: ☰ → Settings and privacy → Settings →
Accounts Center → Password and security → Two-factor → profile →
Authentication app). Approve the "new device" login notification on the
phone first — it makes the CDP session trusted-enough to proceed.

## Notes

- Sidecar routes are `/debug/navigate` + `/debug/eval` + `/debug/page-text`
  — NOT `/session/navigate` (404). `/session/validate` confirms login.
- Sidecar session is logged-in as Themistoklis (FB + business + WABA owner).
- Ad-account data needs `ads_read`/`ads_management` Graph scopes — the
  current Meta token lacks them, so the UI path is the only introspection.
- Business portfolio `cloudless.gr` (id `1558125105019725`), ad account
  `657781691826702`, WABA asset `1073707258453499`.

## meta_2fa_drive.py gates (learned 2026-10-31)

- **Profile gate**: `fb://settings` lands on a profile-switcher if the app
  is on a page/business profile — the script now auto-taps
  "Switch profiles"/"Switch to your main profile" before Accounts Center.
- **ADB resolution**: script resolves `adb` via `ADBS` fallback list
  (bare `adb` → WSL platform-tools `.exe` paths) — no PATH setup needed.
- **Password gate**: wrong `--pw`/`FB_PASSWORD` is now detected
  ("incorrect" on screen) and reported cleanly instead of stalling.
- **Wrong-password flow**: password gate REJECTS silently in FB app —
  check `PASSWORD REJECTED` output and retry with the real FB password
  (the IG/Threads password is NOT the same credential).
- **Email code**: `gmail_code_fetch.py` reads the FB confirmation code
  from Gmail via `%APPDATA%\devin\secrets\gmail-token.json` (auto-refresh).
