---
name: meta-infra-ops
description: Provision Meta ad infra through the Business Management API instead of the browser — system user, app install, non-expiring system-user token, WABA assignment, fresh ad account, appeals pass. Use when rebuilding the Meta/WABA/ad-account stack, when Business Settings is mutation-blocked, or when the scheduled Meta retry runs. Requires a USER token (whatsapp OAuth row) with business_management + ads_management.
---

# Meta Infra Ops — API provisioning chain

Everything the Business Settings wizard does, as one idempotent API script.
Verified 2026-11-22: reads work today; **mutations are gated by the
personal-account restriction** — Meta answers
`OAuthException subcode 2859015` ("Your account is restricted right now")
identically through API and UI. The chain re-runs cleanly the moment the
id/hub identity clears.

## Tool

```bash
# inside social-api (stdlib + repo deps; reuses meta_appeals._fb_token):
python /app/scripts/meta_infra.py probe   # sysusers, WABA status, ad accounts
python /app/scripts/meta_infra.py audit   # debug_token every active row + live IG probe
python /app/scripts/meta_infra.py chain   # full provisioning, idempotent
```

`chain` steps (each skips when already done):

1. `POST /<BM>/system_users` — admin system user `cloudless-bot`
2. `POST /<su>/applications` — install app `business_app=1936126137016578`
3. `POST /<BM>/access_token` — **non-expiring** system-user token
   (`set_token_expires_in_60_days=false`, full scope list)
4. `POST /<WABA>/assigned_users` — `tasks=["MANAGE"]` on WABA 1717901383017723
5. `POST /<BM>/adaccount` — fresh ad account (EUR, Europe/Athens tz 105)
6. appeals pass via `meta_appeals._appeal` on the new account

Transcript: `/app/uploads/meta-infra-log.jsonl` (token never logged).

## Gotchas (live-verified 2026-11-22)

- `POST /system_users` answered `(#100) apps with the capability` — per
  Meta docs the call only needs an admin user token; the likely gates are
  (a) the app isn't claimed by/owned in the BM, or (b) the same personal
  restriction surfacing as a capability error. Re-probe after identity clears;
  if it persists, claim app 1936126137016578 into BM 1558125105019725
  (`POST /<bm>/apps` or Business Settings > Accounts > Apps).
- `POST /adaccount` answered `subcode 2859015` explicitly naming the account
  restriction — this is the canonical signal to wait, not retry.
- The Appeals API, `me/businesses`, `debug_token`, WABA status reads all
  work TODAY with the whatsapp-row USER token.
- WABA `1717901383017723` is `account_review_status: APPROVED` — no account
  recovery needed, only the system-user token mint.
