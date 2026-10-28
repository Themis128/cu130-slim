---
name: linkedin-cm-ops
description: >
  LinkedIn Community Management API ops for app 264925843 (approved
  2026-10-28, Development Tier) — org page posts, comments, socialActions.
  Tool: backend/scripts/linkedin_cm_tool.py (posts/comments/scopes).
  IMPORTANT: the stored org token predates the product approval and lacks
  the new scopes — every version returns NONEXISTENT_VERSION/426.
  Re-authorize the org connection (LINKEDIN_EXTRA_SCOPES +
  w_organization_social, rw_organization_admin) before these calls work.
---

# LinkedIn Community Management Ops

App `264925843` approved for Community Management API (Dev Tier) on
2026-10-28 — unlocks org page posts/comments/reactions via `/rest/*`.

## Tool

    linkedin_cm_tool.py posts <account_id>
    linkedin_cm_tool.py comments <account_id> <post_urn>
    linkedin_cm_tool.py scopes <account_id>

Org account: `9c4451bb-e820-489f-8676-76ddbc788ffe` (cloudless-gr page).

## Activation blocker

The stored org OAuth token predates the product approval — LinkedIn
returns `426 NONEXISTENT_VERSION` for every `Linkedin-Version`. Fix:
re-run the OAuth connect for the org account so the token carries
`w_organization_social`/`rw_organization_admin` + `r_organization_social`.
The connect flow is the normal `/api/v1/auth/oauth/linkedin/authorize`
path (admin UI or tool).

## Development Tier limits

Dev Tier = limited daily call quotas; the app must also have
"Share on LinkedIn" + "Community Management" products active in the
developer portal for posting scopes.
