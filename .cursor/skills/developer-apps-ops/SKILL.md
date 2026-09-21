---
name: developer-apps-ops
description: >-
  Audit and upgrade all social-media developer apps (Meta, TikTok, LinkedIn,
  Twitter/X, Threads, Instagram) against SocialAuto's actual feature usage.
  Covers console URLs and current state per app, the scope↔code audit
  methodology (literal refs + endpoint hints + console API-call counts),
  evidence-based permission pruning, App Review state tracking, and the
  cross-platform OAuth configuration map in app/api/auth.py. Use when the user
  asks to upgrade developer apps, audit permissions/scopes, prepare platform
  review submissions, or when deciding whether a permission is actually needed.
---

# Developer apps ops — audit & upgrade all social developer apps

The task pattern: *"upgrade the developer apps to all my social media based on
my current needs"*. The method that worked (2026-09-21): **prove usage, then
prune or keep** — never request permissions the app can't demonstrate. Meta
explicitly lists requesting unused permissions as a rejection cause.

## App inventory & consoles

| Platform | App | Console | State |
|---|---|---|---|
| Meta | Cloudless `1936126137016578` | `developers.facebook.com/apps/1936126137016578` | Draft submission `2047300442565813` pruned to 21 used perms; blocked on Verification (account restriction) |
| TikTok | Cloudless `7630494700880906241` | `developers.tiktok.com` | **In review** for production (Direct Post). `MEDIA_UPLOAD` until approved |
| LinkedIn | Cloudless | `linkedin.com/developers/apps` | Dev tier. Upgrade only if >5 Company Pages or ads needed — see `linkedin-api-upgrade` |
| Twitter/X | Cloudless | `developer.x.com` | OAuth2 PKCE + OAuth1.0a media creds; scopes verified minimal |
| Threads | (same Meta app family, separate client id) | Meta console | `threads_*` scopes used; `THREADS_CLIENT_ID/SECRET` separate |

Key URLs: Meta submission `…/app-review/submissions/?submission_id=2047300442565813`,
Meta use cases `…/use_cases/`, Testing `…/test`, Verification `…/verification/`.

## The audit method (order matters)

1. **Code-side scope audit** — run the script:
   `python3 .devin/skills/developer-apps-ops/scripts/audit_scope_usage.py`
   It parses every requested scope from `app/api/auth.py`
   (`PLATFORM_SCOPES`, `LINKEDIN_SCOPES`, client `base_scopes`) and counts code
   references (literal permission names — the codebase documents requirements
   in docstrings — plus per-scope endpoint hints in `HINTS`). Zero refs =
   prune candidate.
2. **Console API-call counts** — Meta's use-case permissions tab shows real
   per-permission call counts. **0 lifetime calls = removal candidate** even if
   code exists (dead path). Observed values are recorded in
   `meta-app-review/SKILL.md` → "API-call usage table".
3. **Functional feasibility** — a permission can have code AND be unusable:
   `instagram_manage_comments` had 5 code files but can't run because
   cloudless.gr IG uses **Instagram Business Login** and isn't linked to a
   Page (the permission only works on Page-linked IGs). Check the account's
   `meta_data.login_type` before trusting code refs.
4. **Decide** — keep+complete evidence, or remove. Removal paths (Meta):
   use-case `Actions → Remove` (detaches from app, propagates to draft) and
   submission-level `remove` buttons on the App Review submissions list page
   (for zombie entries not attached to any use case, e.g. WhatsApp).

## OAuth config map (app/api/auth.py)

- `PLATFORM_SCOPES` (line ~1136): the per-platform scope lists sent on connect.
- `LINKEDIN_SCOPES` (line ~220): documented per-scope purpose; `rw_organization_admin`
  intentionally absent until tier upgrade.
- `base_scopes` on each `BaseOAuth2` client: twitter, threads, instagram,
  tiktok. TikTok uses `client_key` + comma-separated scopes (not client_id).
- Stored-account scopes are written at callback — prefer granted scopes from
  the token response over the requested list.
- `/me/permissions` on a decrypted FB user token shows what an existing token
  actually carries — the FB token predates several submission perms, so dev-
  mode tokens must be minted via Graph API Explorer when test calls are needed.

## Meta App Review current state (2026-09-21)

- Draft `2047300442565813`: **21 permissions, all allowed-usage saved**.
- Removed this cycle: whatsapp pair (unattachable), pages_utility_messaging
  (0 calls), instagram_business_manage_insights (0 calls),
  instagram_manage_comments (0 calls + unusable), Human Agent (0 calls).
- Reviewer account `reviewer@cloudless.gr` (EDITOR on admin team) — manage via
  `meta-app-review/scripts/reviewer_account.py`. Creds in `~/.socialauto-reviewer-creds.json`.
- Blockers: (1) business-portfolio connect → "temporarily blocked" (personal
  account restriction, see `meta-account-restriction`); (2) `social.cloudless.gr`
  behind Access SSO — plan: `scripts/cf_access.py review-mode on` right before
  submit, `review-mode off` after approval.
- Full detail: `meta-app-review` skill.

## TikTok current state

- Production review **submitted** after fixing reviewer-facing website URL to
  `https://cloudless.gr` (was the SSO-gated `social.cloudless.gr` — same trap
  as Meta). Terms/privacy on `/en/*` paths. Direct Post toggle ON pending approval.
- Until approval: `MEDIA_UPLOAD` (inbox drafts) — the 3 pending drafts are
  phone-only completions.
- Domain verification for `PULL_FROM_URL` unresolved (`NO_DOMAIN_TOKEN`).
- Details: `tiktok-dev-console`, `tiktok-console-ops` skills.

## Meta console automation notes (playwright MCP)

- Inject the live FB session into the MCP browser via `browser_evaluate`
  `document.cookie` writes — httpOnly blocks reads, not same-domain JS writes.
  Pull cookie name→value from bridge `GET /debug/all-cookies` (sidecar) or
  `/session/cookies`.
- Meta SPA: accordion headers are `href="#"` links; clicking toggles (click
  once, re-read state). Overlays intercept naive clicks — use the MCP
  `browser_click` (real Playwright events), never synthetic JS `.click()`.
- Allowed-usage dialogs list requirements inline: description textbox,
  screencast upload, "0 of N API call(s)", compliance checkbox.
- The submissions list page (`/app-review/submissions/`) shows each draft item
  with a per-item `remove` button + confirm dialog.

## Scripts

- `scripts/audit_scope_usage.py` — scope↔code audit table + prune candidates.
- `meta-app-review/scripts/reviewer_account.py` — reviewer test account lifecycle.
- `scripts/cf_access.py review-mode on|off|status` — temporary hostname bypass
  for review windows (policy prepend/remove on `socialauto-app`, preserves
  admin allow-list).

## Related skills

`meta-app-review`, `meta-oauth-setup`, `meta-account-restriction`,
`tiktok-dev-console`, `tiktok-console-ops`, `linkedin-api-upgrade`,
`twitter-oauth-setup`, `cloudflare-access-paths`, `social-oauth-ops`
