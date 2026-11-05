---
name: linkedin-products-ops
description: >
  Canonical LinkedIn developer-app product/scope process: which product grants
  which scopes, app topology (227354605 org/CM/ads vs 265077368 member),
  live grantability probing (unauthorized_scope_error pre-consent), tier
  upgrade requirements, and the correct token re-auth path. Use for any
  LinkedIn scope/product/tier work — verified against live probes 2026-11-05.
---

# LinkedIn Products & Scopes Ops

Scopes are **granted by products, never chosen**. A scope not provisioned on
the app hard-fails at the authorize step with `unauthorized_scope_error` —
which kills the WHOLE consent, not just that scope. Never ship a scope in the
OAuth request that hasn't been probe-verified on that client_id first.

## Apps (live truth, probed 2026-11-05)

| App | client_id | Products granted | Grantable scopes |
|---|---|---|---|
| `227354605` "Cloudless API App" (PRIMARY — org/CM/ads) | `77tf4oysp8u3fz` | Sign In OIDC, Share, **Community Management Standard Tier** (11-04), Advertising | openid, profile, email, w_member_social, w_organization_social, r_organization_admin, **rw_organization_admin**, r_organization_social, **r_1st_connections_size**, r_ads, **rw_ads**, r_ads_reporting, r_basicprofile, r_events, rw_events |
| `265077368` "cloudless-gr-member" (member surfaces) | `77fd3pui3m2jwm` | Sign In OIDC (Standard Tier, 11-05), **Share on LinkedIn (Default Tier, 11-05)** | openid, profile, email, w_member_social |
| `264925843` "Cloudless Analytics App" (CM-dedicated) | `77j8kzfi1a6jn8` | **Community Management Standard Tier** (11-04), **Events Management Standard Tier** (11-05), Share, Sign In OIDC | **FULL union grantable** — every probed scope incl. *_social_feed, r_member_profileAnalytics, r_member_postAnalytics, r_organization_followers, r_ads(+_reporting), r_events, rw_events, r_dma_portability_self_serve |

Rejected on `227354605` + `265077368` but **grantable on `264925843`**:
`*_social_feed` (3 scopes), `r_member_profileAnalytics`,
`r_member_postAnalytics`, `r_organization_followers`,
`r_dma_portability_self_serve` — probed 2026-11-05. The CM-dedicated
app's grant carries the current-version CM permission set, so the
member-analytics gap is already solved THERE — a consent on
`77j8kzfi1a6jn8` unlocks member surfaces without any ticket.

## Product -> scope map (learn.microsoft.com/linkedin, verified 2026-11)

| Product | Availability | Scopes it grants |
|---|---|---|
| Sign In with LinkedIn (OIDC) | self-serve | openid, profile, email |
| Share on LinkedIn | self-serve | w_member_social |
| Community Management API | application -> Dev tier -> Standard tier | w_/r_organization_social(+_feed), w_member_social(+_feed), rw_/r_organization_admin, r_organization_followers, r_basicprofile, **r_member_profileAnalytics, r_member_postAnalytics, r_1st_connections_size** |
| Advertising API | application -> Dev -> Standard | r_ads, rw_ads, r_ads_reporting, r_organization_admin, r_organization_social, rw_organization_admin, w_organization_social, w_member_social, r_basicprofile, r_1st_connections_size |
| Lead Sync API | application | r_marketing_leadgen_automation, r_ads, r_events, r_organization_admin |
| Conversions API | application | rw_conversions, r_ads |
| Events Management API | self-serve on request | r_events, rw_events |
| Matched Audiences API | application | rw_dmp_segments |
| Member Data Portability (Member) | in-portal modal + business-email verify | r_dma_portability_self_serve |
| Pages Data Portability | access form | r_dma_admin_pages_content, r_dma_admin_pages_list |
| Audience Insights / Media Planning | restricted, qualification survey | app-assigned |
| Talent / Sales Navigator / Compliance | partner programs / closed | — |

IMPORTANT: `r_member_*Analytics`, `*_social_feed`, `r_organization_followers`
are documented as **Community Management API permissions on current API
versions** — NOT a separate product. App 227354605 has CM *Standard* tier but
still rejects them at authorize: its CM grant predates the member-analytics
permission set. Correct fix = **Developer Support Portal ticket** on the app
asking to provision the missing CM permissions (memberAnalytics, feed,
followers). Do NOT create another app for them.

## Rules of the game

- **12-month Dev-tier clock**: CM Dev tier expires if Standard isn't requested
  within 12 months of provisioning.
- **Survey window**: access-request surveys must be completed within **21
  days** or the request closes as "Insufficient Details" (Reapply button
  appears; a hard *rejection* requires a brand-new app).
- **Standard-tier upgrade packet** (what the survey asks):
  downloadable hi-res screen recording of every use case **with US-English
  audio commentary**, test credentials, privacy policy URL, OAuth consent
  screen demo, publish-a-post demo, member-info display on interactions.
- **Dedicated-page gate**: portability products (Member DP) require selecting
  the portability pseudo-page AT APP CREATION; it can't be retrofitted. That
  choice permanently disables Sign In + Share on that app — the two-lane
  architecture (portability app vs write app) is LinkedIn-intended.
- **Tokens**: access = 60 days (5184000s). Programmatic refresh tokens only
  for approved Marketing Developer Platform partners — everyone else re-runs
  the authorize URL: with prior consent, LinkedIn **silently redirects and
  reissues** (no consent screen click) if the member is logged in. This is
  the correct re-auth path — not stored refresh grants.
- **Consent grants REPLACE, never union** — every authorize request must
  carry the complete desired scope set.
- **Rate limits**: Share 150 calls/member/day + 100k/app/day; CM Dev tier
  500/app + 100/member per 24h, no BATCH_GET, social-action webhooks off.

## Tools

```bash
# Probe which scopes a client_id can consent — no secret/login needed
scripts/li_scope_probe.py --client-id 77tf4oysp8u3fz \
  --probe-each --scopes w_member_social r_member_postAnalytics ...

# Doc matrix x live grantability per app
scripts/li_products_matrix.py --app 227354605=77tf4oysp8u3fz \
  --app 265077368=77fd3pui3m2jwm

# Dump portal Auth/Products pages via logged-in CDP browser (:9222)
scripts/li_portal_dump.py 265077368           # needs LinkedIn session
```

`grantable` = passes the authorize step without `unauthorized_scope_error`
(the consent screen itself then shows the real granted set). Probing is a
single unauthenticated GET — client_id is not secret.

## OAuth config (SocialAuto)

- `.env` `LINKEDIN_CLIENT_ID/SECRET` = app 227354605 — stays primary for
  org/CM/ads. `LINKEDIN_EXTRA_SCOPES` MUST stay inside the probe-verified
  set; trimmed 2026-11-05 to `rw_organization_admin r_ads rw_ads
  r_ads_reporting r_1st_connections_size` (the previous union had 7
  ungrantable scopes — guaranteed consent failure). api recreate needed
  after .env change.
- Re-auth when stored token dies (documented recovery):
  `GET /api/v1/auth/oauth/linkedin/authorize?team_id=<uuid>` with admin JWT
  → open URL in any LinkedIn-logged-in browser → silent or consent
  redirect → callback writes fresh tokens to BOTH social_accounts rows.
- A second OAuth client block for the member app (77fd3pui3m2jwm) is only
  needed when member-surface endpoints ship — add a `linkedin_member`
  platform entry then, with its own redirect/callback.

## Remaining product requests (queue)

| Goal | Where | Path |
|---|---|---|
| r_member_postAnalytics/profileAnalytics, *_social_feed, r_organization_followers | 264925843 | ALREADY GRANTABLE — switch OAuth client to `77j8kzfi1a6jn8` (secret from portal Auth tab) + consent |
| r_dma_portability_self_serve (own-data snapshot → connections harvest) | 265077368 | in-portal "Member DP (Member)" modal after business-email verify |
| r_marketing_leadgen_automation (lead forms) | 227354605 | Lead Sync API application (access form) |
| Sign In OIDC + Share on LinkedIn | 265077368 | DONE 2026-11-05 (Standard + Default Tier) |
| CM Standard-tier survey (227354605) | — | submitted; survey needs ~2min narrated screen recording + registered address — see linkedin-access-forms |
