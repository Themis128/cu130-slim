---
name: meta-appeals-ops
description: File real numbered Meta appeals for restricted ad accounts and Business Managers through the official Appeals API (ad_review_requests / bm_review_requests -> appeal_case_id). Use when a Meta ad account, BM, or portfolio is restricted/banned and you need a trackable case ID instead of the browser wizard. Requires business_management scope on the stored Facebook token.
---

# Meta Appeals Ops

Meta's **official Appeals API** (Business Management API) files numbered
appeals programmatically — returns `appeal_case_id`, the real case ID the
browser wizard never mints. Doc:
`developers.facebook.com/docs/marketing-api/business-asset-management/guides/appeals`

## Endpoints

- `POST /v21.0/<parent_bm_id>/ad_review_requests` — appeal ad accounts
  (`ad_account_ids` list:numeric + `app` = partner app id). Max 50/call.
- `POST /v21.0/<parent_bm_id>/bm_review_requests` — appeal Business Managers
  (`business_manager_ids` + `app`).

Per-entity `status` in the response:

- `appeal_creation_success` — case created, `appeal_case_id` returned.
- `appeal_entity_invalid` — not eligible (e.g. review window expired —
  the "too much time has passed" dead-account class).
- `appeal_creation_failure` — eligible but pipeline refused (retry later).

## Token requirements

`business_management` scope (the cloudless.gr FB user token already carries
it — verify with `debug_token`), admin on the appealed assets, and the token
must be a **User** token (Page tokens 400 on `/me/businesses`). The SocialAuto
`facebook` account row stores the FB Login user token.

## Tool

```bash
# inside social-api (stdlib + repo deps only):
python /app/scripts/meta_appeals.py status            # token + BM + restriction map
python /app/scripts/meta_appeals.py appeal-accounts <ad_account_id> [...]
python /app/scripts/meta_appeals.py appeal-bm <bm_id> [...]
```

`status` prints: token type/scopes, owned Business Managers, and each ad
account's restriction info — run it before every appeal to pick the right
lane (dead accounts short-circuit as `appeal_entity_invalid`, saving a call).

Every run appends a JSONL transcript to `/app/uploads/meta-appeals-log.jsonl`
(never prints the token).

## Gotchas (verified 2026-11-22)

- A "temporarily blocked" Business Settings page is downstream of the
  personal-account restriction — appeal the BM/person, not just the ad account.
- Ad accounts restricted >~ a few months answer `appeal_entity_invalid`
  (window closed) — the fix is a NEW ad account, not another appeal.
- The `app` parameter is the Meta developer-app id that owns the
  `business_management` grant — read it from `debug_token`'s `app_id`,
  not from env guesswork.
- `appeal_case_id` IS the trackable number the support wizard fails to mint —
  archive it into `meta-support-comm` (meta-support-ops skill) when it lands.
