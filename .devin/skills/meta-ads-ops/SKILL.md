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

## Restriction taxonomy (what the text means)

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

## Notes

- Sidecar routes are `/debug/navigate` + `/debug/eval` + `/debug/page-text`
  — NOT `/session/navigate` (404). `/session/validate` confirms login.
- Sidecar session is logged-in as Themistoklis (FB + business + WABA owner).
- Ad-account data needs `ads_read`/`ads_management` Graph scopes — the
  current Meta token lacks them, so the UI path is the only introspection.
- Business portfolio `cloudless.gr` (id `1558125105019725`), ad account
  `657781691826702`, WABA asset `1073707258453499`.
