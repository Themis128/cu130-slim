---
name: linkedin-cm-ops
description: >
  LinkedIn Community Management API ops for app 264925843 (Standard Tier
  since 2026-11-04; Dev Tier approved 2026-10-28) — org page posts,
  comments, socialActions. Tool: backend/scripts/linkedin_cm_tool.py
  (posts/comments/scopes). Token healed 2026-11-04 via refresh_token
  grant — no consent click needed. IMPORTANT: Linkedin-Version must be
  an ACTIVE monthly version (202312/202404/202510 all return
  NONEXISTENT_VERSION/426; 202608/202609/202610 verified live).
---

# LinkedIn Community Management Ops

App `264925843` — Community Management API **Standard Tier** (email
approval 2026-11-04; was Dev Tier 2026-10-28) — org page
posts/comments/reactions/stats via `/rest/*`.

## Tool

    linkedin_cm_tool.py posts <account_id>
    linkedin_cm_tool.py comments <account_id> <post_urn>
    linkedin_cm_tool.py scopes <account_id>

Org account: `9c4451bb-e820-489f-8676-76ddbc788ffe` (cloudless-gr page,
`urn:li:organization:108614163`). Scripts are standalone — run from
`/tmp` inside social-api (`/app/scripts` bind-mount is :ro):

    docker compose cp social-automation/backend/scripts/linkedin_cm_tool.py \
      social-api:/tmp/cm.py
    docker compose exec -T -w /app social-api env PYTHONPATH=/app \
      python /tmp/cm.py scopes 9c4451bb-e820-489f-8676-76ddbc788ffe

## Activation — RESOLVED 2026-11-04

Two separate failure classes were conflated as "token predates approval":

1. **Stale version pin.** `NONEXISTENT_VERSION/426` was actually the
   `Linkedin-Version` header — LinkedIn only accepts ACTIVE monthly
   versions; retired pins silently kill every call. Verified dead:
   `202312` (consent_healers), `202404` (linkedin_ads leadFormResponses
   — silently returned `[]`), `202510` (both *_tool.py scripts).
   Live: `202608`/`202609`/`202610` (202510 was already inactive on
   2026-11-04 despite being ~13mo old — do not assume the 12-month SLA).
   Production client (`linkedin_api.py`/`analytics_sync.py`/`auth.py`)
   is on `202608` — fine.
2. **Stale token.** The stored org token predated the product approval.
   Fix was NOT a browser re-auth: a `refresh_token` grant minted a
   fresh access+refresh pair carrying the CM scopes
   (`w_organization_social`, `r_organization_social`,
   `r_organization_admin`, `w_member_social`, `r_ads`,
   `r_ads_reporting`). Written back to both social_accounts rows
   (`9c4451bb` org + `18d5cd59` person), expiry 2027-01-03.
   Note: `rw_organization_admin` (LINKEDIN_EXTRA_SCOPES) is still NOT
   granted — it needs a fresh consent screen, not a refresh.

Verified live 2026-11-04: `/rest/posts` 200 (org feed),
`/rest/organizations/108614163` 200, `/rest/organizationAcls` 200.

If a re-auth is ever needed: `GET /api/v1/auth/oauth/linkedin/authorize
?team_id=10024d25-…` (admin JWT) → consent in any logged-in browser →
callback on social.cloudless.gr. If ingress is down, copy `?code&state`
from the dead URL bar and replay on `localhost:8083`. LinkedIn sidecar
(:9225) session is dead (redirect_loop circuit); bridge :9223 profile
hit a "Welcome back" password wall — both are human gates today.

## Standard Tier notes

Standard Tier = full Community Management quota (Dev Tier was the
capped one). The app must keep "Share on LinkedIn" + "Community
Management" products active for posting scopes.

## v2 fallback (still live)

`linkedin_api.py` falls back to legacy /v2/ endpoints on 403/426:
- `create_post` → POST /v2/ugcPosts
- `create_comment` → POST /v2/socialActions/{urn}/comments
- `delete_post` → DELETE /v2/ugcPosts/{urn}
- list → GET /v2/ugcPosts?q=authors&authors=List(<enc urn>) (literal parens!)
Not v2-covered (rest-only, now unlocked by Standard Tier):
multi-image/video/doc posts, org stats, follower counts.
