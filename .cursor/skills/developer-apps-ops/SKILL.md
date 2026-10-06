---
name: developer-apps-ops
description: >-
  Audit, configure, and upgrade every social developer app/console: Meta (OAuth, App Review, business verification, account restrictions, support reports), LinkedIn API tier upgrade, Twitter/X OAuth, TikTok dev console. Use for scopes/permissions audits, review submissions, app config, and platform-console questions.
---

# Developer Apps Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Developer apps ops — audit & upgrade all social developer apps | `developer-apps-ops` |
| Meta Account Restriction & Appeal | `developer-apps-ops` → `meta-account-restriction/` |
| Meta App Review Management | `developer-apps-ops` → `meta-app-review/` |
| Meta OAuth Setup (Facebook + Instagram + Threads) | `developer-apps-ops` → `meta-oauth-setup/` |
| Meta Support Report (Report a Problem) | `developer-apps-ops` → `meta-support-report/` |
| LinkedIn API Developer Access Tier Upgrade | `developer-apps-ops` → `linkedin-api-upgrade/` |
| Twitter/X OAuth Setup | `developer-apps-ops` → `twitter-oauth-setup/` |
| TikTok Developer Console | `developer-apps-ops` → `tiktok-dev-console/` |

## Developer apps ops — audit & upgrade all social developer apps

The task pattern: *"upgrade the developer apps to all my social media based on
my current needs"*. The method that worked (2026-09-21): **prove usage, then
prune or keep** — never request permissions the app can't demonstrate. Meta
explicitly lists requesting unused permissions as a rejection cause.

### App inventory & consoles

| Platform | App | Console | State |
|---|---|---|---|
| Meta | Cloudless `1936126137016578` | `developers.facebook.com/apps/1936126137016578` | Draft submission `2047300442565813` pruned to 21 used perms; blocked on Verification (account restriction) |
| TikTok | Cloudless `7630494700880906241` | `developers.tiktok.com` | **In review** for production (Direct Post). `MEDIA_UPLOAD` until approved |
| LinkedIn | Cloudless | `linkedin.com/developers/apps` | Dev tier. Upgrade only if >5 Company Pages or ads needed — see `developer-apps-ops` |
| Twitter/X | Cloudless | `developer.x.com` | OAuth2 PKCE + OAuth1.0a media creds; scopes verified minimal |
| Threads | (same Meta app family, separate client id) | Meta console | `threads_*` scopes used; `THREADS_CLIENT_ID/SECRET` separate |

Key URLs: Meta submission `…/app-review/submissions/?submission_id=2047300442565813`,
Meta use cases `…/use_cases/`, Testing `…/test`, Verification `…/verification/`.

### The audit method (order matters)

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

### OAuth config map (app/api/auth.py)

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

### Meta App Review current state (2026-09-21)

- Draft `2047300442565813`: **21 permissions, all allowed-usage saved**.
- Removed this cycle: whatsapp pair (unattachable), pages_utility_messaging
  (0 calls), instagram_business_manage_insights (0 calls),
  instagram_manage_comments (0 calls + unusable), Human Agent (0 calls).
- Reviewer account `reviewer@cloudless.gr` (EDITOR on admin team) — manage via
  `meta-app-review/scripts/reviewer_account.py`. Creds in `~/.socialauto-reviewer-creds.json`.
- Blockers: (1) business-portfolio connect → "temporarily blocked" (personal
  account restriction, see `developer-apps-ops`); (2) `social.cloudless.gr`
  behind Access SSO — plan: `scripts/cf_access.py review-mode on` right before
  submit, `review-mode off` after approval.
- Full detail: `developer-apps-ops` skill.

### TikTok current state

- Production review **submitted** after fixing reviewer-facing website URL to
  `https://cloudless.gr` (was the SSO-gated `social.cloudless.gr` — same trap
  as Meta). Terms/privacy on `/en/*` paths. Direct Post toggle ON pending approval.
- Until approval: `MEDIA_UPLOAD` (inbox drafts) — the 3 pending drafts are
  phone-only completions.
- Domain verification for `PULL_FROM_URL` unresolved (`NO_DOMAIN_TOKEN`).
- Details: `developer-apps-ops`, `tiktok-console-ops` skills.

### Meta console automation notes (playwright MCP)

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

### Scripts

- `scripts/audit_scope_usage.py` — scope↔code audit table + prune candidates.
- `meta-app-review/scripts/reviewer_account.py` — reviewer test account lifecycle.
- `scripts/cf_access.py review-mode on|off|status` — temporary hostname bypass
  for review windows (policy prepend/remove on `socialauto-app`, preserves
  admin allow-list).

### Related skills

`developer-apps-ops`, `developer-apps-ops`, `developer-apps-ops`,
`developer-apps-ops`, `tiktok-console-ops`, `developer-apps-ops`,
`developer-apps-ops`, `cloudflare-ops`, `social-accounts-manager`

## Meta Account Restriction & Appeal

Diagnose and resolve Meta account restrictions that block business verification
and App Review. Use when the personal Facebook account is restricted from
advertising, when business verification shows "Your account is restricted right
now", or when the Account Quality page shows a disabled ad account.

### Current restriction (as of 2026-09-11)

| Field | Value |
|-------|-------|
| Personal account | Themistoklis Baltzakis |
| Ad account ID | `657781691826702` |
| Ad account status | **Disabled** |
| Restricted since | Jan 24, 2021 |
| Restriction type | Permanent advertising restriction |
| Reason | Non-compliance with Advertising Standards (business assets) |
| Appeal available | **No** — "Too much time has passed since this account was disabled, so this decision can't be reviewed" |
| Business portfolio | `cloudless.gr` (ID: `1558125105019725`) — No advertising issues |
| Business verification | **Blocked** — requires admin in good standing |

### What's restricted

The personal ad account restriction blocks:
- Can't create or run ads
- Can't use or share audiences
- Can't use Meta Pixel, offline event sets, or custom conversions
- Can't use app SDKs to send app events
- Can't manage advertising assets or people for businesses
- **Can't complete business verification** (requires admin in good standing)

### Key URLs

| Resource | URL |
|----------|-----|
| Account Status | `https://www.facebook.com/account_status` |
| Account Quality | `https://www.facebook.com/accountquality` |
| Business Support Home | `https://www.facebook.com/business-support-home/` |
| Ad account detail | `https://www.facebook.com/business-support-home/1134463867/657781691826702/` |
| Business portfolio detail | `https://www.facebook.com/business-support-home/1558125105019725/` |
| Meta AI Business Assistant | Available from Business Support Home sidebar |

### Diagnosis flow

#### 1. Check Account Status (personal)

Navigate to `https://www.facebook.com/account_status`. This shows the personal
account health. As of 2026-09-11, this page shows "Your account looks good!" —
**this is misleading**. The restriction is on the advertising side, not the
personal account integrity side.

#### 2. Check Business Support Home — My Accounts

Navigate to `https://www.facebook.com/business-support-home/?landing_page=overview`.
This shows:
- **Facebook account**: Themistoklis Baltzakis — "Account restricted"
- **Business portfolios**: cloudless.gr — "No advertising issues"

Click the restricted Facebook account to see:
- The restriction date and reason
- What's disabled (ad account, audiences, pixels, etc.)
- "What you can do" section (may show no appeal option)

#### 3. Check ad account detail

Navigate to `https://www.facebook.com/business-support-home/1134463867/657781691826702/`.
This shows the specific ad account restriction. If the appeal window has
expired, the text will say:

> "Too much time has passed since this account was disabled, so this decision
> can't be reviewed."

There will be **no "Request Review" button**.

#### 4. Chat with Meta AI Business Assistant

From Business Support Home, click "Meta AI business assistant" in the sidebar.
The AI can:
- Diagnose the restriction type
- Confirm whether it's on the personal account or business portfolio
- **Cannot** create a formal support ticket
- **Cannot** escalate to a live agent (may say "support team is at full capacity")
- Direct you to Account Quality for appeals

### Resolution options

#### Option A: Submit an appeal (if available)

If the ad account detail page shows a "Request Review" button:
1. Click "Request Review"
2. Select a reason for the review
3. Write a clear, factual explanation:
   - Acknowledge the issue
   - Explain what you've done to fix it
   - Describe steps to prevent future violations
4. Submit — processing takes 48 hours to 2 weeks
5. Track in Support Inbox

**Current status**: Appeal is NOT available (time window expired).

#### Option B: Create a new ad account

The restriction is on the specific ad account (`657781691826702`), not on
creating new ad accounts. From the ad account detail page:
1. Click "Go to Business Settings"
2. Add a new ad account in the business portfolio
3. Use the new ad account for advertising going forward

**Note**: This does NOT resolve the business verification block — the
restriction is on the personal account, not just the ad account.

#### Option C: Add a second admin

The fastest path to unblock business verification:
1. Have a trusted person (with a Facebook account in good standing) log in
2. Add them as an admin of the Cloudless app:
   `https://developers.facebook.com/apps/1936126137016578/roles/`
3. Add them as an admin of the cloudless.gr business portfolio
4. Have them complete business verification
5. Once verified, the App Review submission can proceed

#### Option D: Report a problem to Facebook

Use Facebook's "Report a Problem" feature to escalate:
1. Go to `https://www.facebook.com/`
2. Click profile picture (top right) > Help & support > Report a problem
3. Click "Include" (to include logs and diagnostics)
4. Write a detailed description of the issue
5. Attach screenshots of the restriction and the App Review block
6. **Required**: Use "Capture Screen" to select a screen area (cannot be
   automated via Playwright — requires manual screen selection)
7. Click "Submit report"

See the `developer-apps-ops` skill for the report template and screenshot
requirements.

#### Option E: Meta Business Help Center contact forms

Try these direct contact forms (availability varies by account):
- `https://www.facebook.com/help/contact/6359191084165019` — General object
- `https://www.facebook.com/help/contact/2725860640553550` — Ad account
  restriction appeal (may redirect if not eligible)

### Important notes

- **Account Status vs Account Quality**: Account Status
  (`facebook.com/account_status`) shows personal account health and may show
  "looks good" even when advertising features are restricted. Account Quality
  (`facebook.com/accountquality`) shows advertising-specific restrictions.
- **Permanent restrictions**: If Meta says "too much time has passed", the
  self-serve appeal path is closed. The only options are: add a second admin,
  create a new ad account, or escalate through support channels.
- **Business verification requires an admin in good standing**: The admin who
  starts verification must not have advertising restrictions on their personal
  account.
- **The Meta AI business assistant is a chatbot**: It can diagnose but cannot
  create formal support tickets or escalate to live agents.

### Scripts

- `scripts/check-account-status.py` — Check personal account status via Graph API
- `scripts/check-ad-account.py` — Check ad account restriction status
- `scripts/check-business-portfolio.py` — Check business portfolio health
- `scripts/print-appeal-template.py` — Print an appeal template for submission

### Related skills

- `developer-apps-ops` — App Review submission and permission management
- `developer-apps-ops` — Report a Problem to Facebook support
- `developer-apps-ops` — OAuth configuration for Meta platforms

## Meta App Review Management

Manage the Meta App Review submission for the Cloudless app, including business
verification, permission allowed-usage, screencasts, reviewer instructions, and
final submission. Use when checking App Review status, completing permission
requirements, uploading screencasts, or debugging the "Submit for review" button
being disabled.

### App identifiers

- **App ID**: `1936126137016578`
- **App name**: Cloudless
- **App Review submission ID**: `2047300442565813`
- **Business portfolio**: `cloudless.gr` (ID: `1558125105019725`)
- **Privacy policy**: `https://social.cloudless.gr/privacy`
- **Data deletion**: `https://social.cloudless.gr/data-deletion`
- **Webhook URL**: `https://social.cloudless.gr/api/v1/messenger/webhook`
- **Webhook verify token**: `cloudless_messenger_verify`

### Key URLs

| Resource | URL |
|----------|-----|
| App Dashboard | `https://developers.facebook.com/apps/1936126137016578` |
| App Review submission | `https://developers.facebook.com/apps/1936126137016578/app-review/submissions/?submission_id=2047300442565813` |
| Business verification | `https://developers.facebook.com/apps/1936126137016578/app-review/verification` |
| Business Support Home | `https://www.facebook.com/business-support-home/` |
| Account Status | `https://www.facebook.com/account_status` |
| Account Quality | `https://www.facebook.com/accountquality` (redirects to BSH if restricted) |

### Submission state (2026-09-21)

The draft submission was pruned to only permissions SocialAuto actually uses —
Meta rejects submissions containing undemonstrable permissions. All remaining
items show **Edit** (allowed-usage saved); nothing is incomplete.

#### Removed from the draft (with evidence)

| Item | Why removed |
|------|-------------|
| `whatsapp_business_messaging` + `_management` | Can't attach to the app — WhatsApp onboarding is Meta-blocked; can't demo. Re-add via WhatsApp use case when onboarding unblocks. |
| `pages_utility_messaging` | 0 lifetime API calls; no code refs (utility message templates only — messenger bot uses `pages_messaging`). |
| `instagram_business_manage_insights` | 0 lifetime calls; analytics uses `instagram_manage_insights` (FB-login family) instead. |
| `instagram_manage_comments` | 0 calls AND can't run — cloudless.gr IG uses Instagram Business Login and is not linked to a Page. The working `instagram_business_manage_comments` stays. |
| `Human Agent` feature | 0 calls; no `human_agent` tag in messenger code. |

#### How to remove a permission

Two levels: (a) use-case level — `Use cases → Customize → Permissions and features`
tab → row `Actions → Remove` (removes from app + propagates to the draft);
(b) submission level — `App Review → submissions` list page, per-item `remove`
button (for items whose permission isn't attached to any use case, e.g. the
WhatsApp zombies). Confirm dialog: "Yes, remove".

#### Remaining permissions (all allowed-usage saved)

pages_show_list, pages_manage_metadata, pages_messaging, business_management,
pages_read_engagement, instagram_business_basic, instagram_business_manage_messages,
pages_read_user_content, pages_manage_posts, instagram_business_content_publish,
pages_manage_engagement, threads_basic, instagram_content_publish,
instagram_manage_messages, instagram_business_manage_comments, read_insights,
ads_read, ads_management, public_profile, instagram_manage_insights, instagram_basic

### Wizard steps status

| Step | Status |
|------|--------|
| Verification | **BLOCKED** — connecting `cloudless.gr` portfolio returns "temporarily blocked from performing this action" (personal-account restriction) |
| App settings | Done — icon, privacy URL `social.cloudless.gr/privacy` (public 200), category "Business and pages", contact email |
| Allowed usage | Done — all items saved |
| Data handling | Done — pre-filled reviewed (Cloudflare processor, controller Baltzakis Themistoklis, Greece) |
| Reviewer instructions | Done — includes test account creds |

### Reviewer test account

- `reviewer@cloudless.gr` — EDITOR role on the admin (enterprise) team.
- Created directly in DB (no own team → team resolution lands on admin team).
- Credentials are in the submission's access-code field AND the instructions text.
- **Delete this account after review concludes.**

### Cloudflare Access exposure (pending decision implementation)

`social.cloudless.gr` root is behind Access SSO (302 → cloudflareaccess.com) —
same wall that got TikTok rejected. Chosen fix: **temporary full Access bypass**
on the hostname during the review window (app's own auth still gates everything;
exposure = login page only). Apply via `cloudflare-ops` skill right
before submitting, revert after approval. Not applied yet — no benefit while
Verification is blocked.

### API-call usage table

Each use-case permissions tab shows real per-permission API call counts — use it
to prove usage before deciding keep/remove: 0 calls = removal candidate.
Observed: ads_management 2.6k, business_management 4k, pages_messaging 8.8k,
pages_manage_metadata 9.4k, pages_read_engagement 9.4k, instagram_basic 2.4k,
public_profile 2.5k, instagram_business_manage_messages 366,
instagram_content_publish 360, instagram_manage_messages 8.

### Current blocker

**Business verification is blocked** by a permanent advertising restriction on
the personal Facebook account (Themistoklis Baltzakis, ad account
`657781691826702`, disabled Jan 24, 2021). Meta says "too much time has passed"
and the decision cannot be reviewed through Account Quality. The same
restriction blocks: business-portfolio connect ("temporarily blocked"),
WhatsApp onboarding ("temporarily blocked").

See the `developer-apps-ops` skill for resolution steps.

### Screencast generation

The screencast was generated from SocialAuto screenshots using ffmpeg inside
the `social-api` container:

```bash
## Generate screencast from screenshots (inside social-api container)
docker compose exec -T social-api bash -c '
  mkdir -p /tmp/screencast-output
  ffmpeg -y -framerate 5 -i /tmp/screencast-frames/frame_%02d.png \
    -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p \
    -s 1280x720 /tmp/screencast-output/cloudless-screencast.mp4
'
## Copy back to host
docker compose cp social-api:/tmp/screencast-output/cloudless-screencast.mp4 \
  ./cloudless-screencast.mp4
```

The screencast is ~45 seconds, 1280×720, H.264/MP4, ~365 KB. It contains:
landing page, login, dashboard, messenger UI, bot health, accounts, privacy
policy, webhook response, data deletion policy.

### File upload workaround

Meta's file input is intercepted by an overlay div (`<div class="_3ixn"></div>`).
Use DOM evaluation to trigger the file input directly:

```js
// Find the file input by ID (changes each render — inspect the DOM)
const input = document.getElementById('js_96');
if (input) input.click();
```

Then handle the file chooser:
```js
await fileChooser.setFiles(["/workspace/cloudless-screencast.mp4"]);
```

### Checkbox click workaround

Meta's compliance checkbox is intercepted by a modal overlay. Click directly:

```js
const checkboxes = document.querySelectorAll('input[type="checkbox"]');
for (const cb of checkboxes) {
  if (cb.closest('[role="dialog"]')) { cb.click(); break; }
}
```

### Save button workaround

The Save button inside the allowed-usage dialog may not be found by role.
Use DOM evaluation:

```js
const buttons = document.querySelectorAll('[role="button"]');
for (const btn of buttons) {
  if (btn.textContent.trim() === 'Save' && btn.closest('[role="dialog"]')) {
    btn.click(); break;
  }
}
```

### Scripts

- `scripts/check-review-status.py` — Check App Review submission status via Graph API
- `scripts/check-business-verification.py` — Check business verification status
- `scripts/list-permissions.py` — List all permissions and their review status
- `scripts/generate-screencast.py` — Generate a screencast from screenshots using ffmpeg
- `scripts/reviewer_account.py` — Manage the reviewer test account: `create`
  (EDITOR on admin team, creds → `~/.socialauto-reviewer-creds.json`),
  `verify` (login + account count), `status`, `delete` (post-review cleanup)

### Console automation via playwright MCP

- Inject the live FB session into the MCP browser with `document.cookie`
  writes (`browser_evaluate`) — see `session-ops` → "bridge → MCP".
  The MCP browser is contention-free vs the shared bridge's X-Platform hold.
- Accordion headers are `href="#"` links — clicking toggles, so click once
  and re-read the snapshot before the next action.
- Overlays intercept synthetic JS clicks — always use `browser_click` (real
  Playwright events).
- Permission removal: use-case `Actions → Remove` for attached perms;
  submission-level `remove` buttons on `/app-review/submissions/` for
  unattached/zombie entries (e.g. WhatsApp perms that can't attach while
  onboarding is blocked).
- Use-case `permissions` tab shows real per-permission API call counts —
  0 lifetime calls = evidence for removal.
- WhatsApp perms attach only via the use case's **API Setup** flow (provisions
  the test number), not the generic permissions table — blocked while the
  personal account is restricted ("Onboarding failure — temporarily blocked").

### Related skills

- `developer-apps-ops` — Resolve personal account restrictions blocking verification
- `developer-apps-ops` — Submit a "Report a Problem" to Facebook support
- `developer-apps-ops` — OAuth configuration for Facebook/Instagram/Threads
- `messenger-ops` — Messenger webhook and bot configuration

## Meta OAuth Setup (Facebook + Instagram + Threads)

Set up OAuth for all three Meta platforms using a single Meta developer app.
Use when configuring Facebook, Instagram, or Threads OAuth in SocialAuto,
registering redirect URIs, retrieving Threads App ID/Secret, or debugging
Meta OAuth errors.

### Architecture

All three platforms share one Meta app but have **different OAuth endpoints and credentials**:

| Platform | Authorize URL | Token URL | App ID used |
|-----------|--------------|-----------|-------------|
| Facebook | `https://www.facebook.com/dialog/oauth` | `https://graph.facebook.com/oauth/access_token` | Main Meta App ID |
| Instagram (FB Login) | `https://www.facebook.com/dialog/oauth` | `https://graph.facebook.com/oauth/access_token` | Main Meta App ID |
| Threads | `https://threads.net/oauth/authorize` | `https://graph.threads.net/oauth/access_token` | **Threads App ID** (separate) |

**Key**: Threads has its own App ID and App Secret, found in App Dashboard > Settings > Basic > Threads App ID/Secret. Facebook and Instagram share the main Meta App ID and Secret.

### Existing Meta app

- **App ID**: `1936126137016578`
- **App Secret**: (in `.env` as `FACEBOOK_CLIENT_SECRET` / `INSTAGRAM_CLIENT_SECRET`)
- **App Dashboard**: https://developers.facebook.com/apps/1936126137016578
- **Facebook Page**: `116436681562585` (cloudless.gr)
- **Meta Business Portfolio**: `1558125105019725`

### Required redirect URIs

All redirect URIs must use **HTTPS** (Meta rejects `http://` except for localhost testing in development mode). Register these in the App Dashboard:

```
https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback
https://social.cloudless.gr/api/v1/auth/oauth/instagram/callback
https://social.cloudless.gr/api/v1/auth/oauth/threads/callback
```

For local development testing (only works if app is in development mode):
```
http://localhost:8083/api/v1/auth/oauth/facebook/callback
http://localhost:8083/api/v1/auth/oauth/instagram/callback
http://localhost:8083/api/v1/auth/oauth/threads/callback
```

### Required scopes

#### Facebook (Pages API)
```
public_profile, email, pages_show_list, pages_read_engagement, pages_manage_posts
```

#### Instagram (via Facebook Login flow)
```
instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, pages_manage_posts
```

**Note**: If using Business Login for Instagram (Instagram credentials, not Facebook), the scopes change to:
```
instagram_business_basic, instagram_business_content_publish
```
And the authorize URL changes to `https://www.instagram.com/oauth/authorize` with a separate Instagram App ID.
SocialAuto currently uses the Facebook Login flow.

#### Threads
```
threads_basic, threads_content_publish, threads_manage_insights, threads_manage_replies
```

Optional Threads scopes (add if needed):
- `threads_read_replies` — for reading replies
- `threads_delete` — for deleting posts
- `threads_keyword_search` — for keyword search
- `threads_location_tagging` — for location tags
- `threads_manage_mentions` — for mention management
- `threads_profile_discovery` — for profile discovery

### Setup steps

#### 1. Configure the Meta app

1. Go to https://developers.facebook.com/apps/1936126137016578
2. Ensure these use cases are added:
   - "Manage everything on your Page" (for Facebook + Instagram)
   - "Access the Threads API" (for Threads)
3. Under each use case, add the required permissions listed above.

#### 2. Register redirect URIs

**For Facebook + Instagram** (Facebook Login settings):
1. App Dashboard > Use cases > Customize > Facebook Login > Settings
2. Add all three Facebook/Instagram redirect URIs to "Valid OAuth Redirect URIs"
3. Save

**For Threads** (separate settings):
1. App Dashboard > Use cases > Customize > Access the Threads API > Settings
2. Add the Threads redirect URI to "Client OAuth Settings"
3. Save

#### 3. Get Threads App ID and Secret

1. App Dashboard > Settings > Basic
2. Scroll to find "Threads App ID" and "Threads App Secret"
3. These are **different** from the main Meta App ID/Secret

#### 4. Add Threads testers

1. App Dashboard > App roles > Roles > Add People
2. Select "Threads Tester" role
3. The invited user must accept at threads.net/settings/account > Website permissions

#### 5. Update `.env`

```bash
## Facebook (main Meta app credentials)
FACEBOOK_CLIENT_ID=1936126137016578
FACEBOOK_CLIENT_SECRET=<main_meta_app_secret>
FACEBOOK_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback

## Instagram (same main Meta app credentials)
INSTAGRAM_CLIENT_ID=1936126137016578
INSTAGRAM_CLIENT_SECRET=<main_meta_app_secret>
INSTAGRAM_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/instagram/callback

## Threads (separate Threads App ID/Secret)
THREADS_CLIENT_ID=<threads_app_id>
THREADS_CLIENT_SECRET=<threads_app_secret>
THREADS_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/threads/callback
```

#### 6. Restart social-api

```bash
cd /home/tbaltzakis/cu130-slim
docker compose restart social-api
curl http://localhost:8083/health
```

#### 7. Connect accounts

Open http://localhost:8082/accounts and click Connect for each platform.

#### Facebook account model

When a user connects Facebook, SocialAuto stores:

1. **User account** (type=`user`, `is_business=False`) — the Facebook user who authorized, with a long-lived user token (~60 days). This is the main account.
2. **Page accounts** (type=`page`, `is_business=True`) — one per managed Page, each with a permanent Page token. These are created automatically during OAuth and when **Sync Business** is clicked.

The `Sync Business` button on the User account calls `GET /me/accounts` with the user token to discover Pages. If it fails with `(#100) Tried accessing nonexisting field (accounts)`, the stored token is a Page token instead of a User token — disconnect and reconnect Facebook to fix.

Publishing uses the Page accounts (which have permanent Page tokens), not the User account.

### Token lifecycle

| Platform | Short-lived | Long-lived | Refresh |
|----------|-------------|------------|---------|
| Facebook | ~1 hour | ~60 days (user token) | Re-exchange before expiry |
| Facebook Page | — | Permanent (page token) | No refresh needed |
| Instagram | ~1 hour | ~60 days (via FB exchange) | Re-exchange before expiry |
| Threads | ~1 hour | ~60 days | `GET /refresh_access_token` |

#### Facebook long-lived token exchange

```bash
GET https://graph.facebook.com/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id={APP_ID}
  &client_secret={APP_SECRET}
  &fb_exchange_token={SHORT_LIVED_TOKEN}
```

#### Threads long-lived token exchange

```bash
GET https://graph.threads.net/access_token
  ?grant_type=th_exchange_token
  &client_secret={THREADS_APP_SECRET}
  &access_token={SHORT_LIVED_TOKEN}
```

#### Threads token refresh

```bash
GET https://graph.threads.net/refresh_access_token
  ?grant_type=th_refresh_token
  &access_token={LONG_LIVED_TOKEN}
```

### Instagram Business Account requirement

Instagram publishing requires a **Business or Creator account** linked to a Facebook Page. The callback handler:
1. Exchanges the short-lived FB token for a long-lived token
2. Fetches `/me/accounts` (Facebook Pages)
3. Looks for `instagram_business_account` on each page
4. Falls back to `page_backed_instagram_accounts`
5. Falls back to Business Manager `instagram_accounts`

If no IG Business Account is found, the FB user info is stored as a fallback (posting won't work until the IG-FB Page link is established).

#### Fixing the IG-FB Page connection

1. Go to `facebook.com/settings/?tab=linked_instagram`
2. Click "Review connection" next to the cloudless.gr Instagram account
3. Enter the Instagram password
4. Approve all permissions
5. If "Business Account Not Allowed to Advertise" appears — ignore it (that's about ads, not the API)

### Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `redirect_uri` mismatch | URI not registered or trailing slash mismatch | Check App Dashboard settings exactly match `.env` |
| `Invalid scope` | Scope not added to the app | Add the permission under the use case in App Dashboard |
| `Instagram Business Account not found` | IG account not linked to FB Page or is Personal | Switch IG to Business, complete "Review connection" |
| Threads `invalid_client_id` | Using main Meta App ID instead of Threads App ID | Use the Threads-specific App ID from Settings > Basic |
| `code 190` | Token expired | Re-connect the account |
| `App not in development mode` for localhost | App is live | Use the production HTTPS redirect URI through the Cloudflare tunnel |

### Scripts

- `scripts/verify-oauth-urls.py` — Generate and verify OAuth authorize URLs for all three Meta platforms
- `scripts/check-meta-token.py` — Check if a Meta access token is valid and show its expiry

## Meta Support Report (Report a Problem)

Submit a "Report a Problem" to Facebook through the Help & support menu. Use
when escalating account restrictions, business verification blocks, or App
Review issues that the Meta AI business assistant cannot resolve through
formal support channels.

### When to use this

- The Meta AI business assistant cannot create a formal support ticket
- The Account Quality "Request Review" button is not available (appeal window expired)
- Business verification is blocked by a personal account restriction
- You need to escalate to Facebook's human review team

### How to access

1. Go to `https://www.facebook.com/`
2. Click your **profile picture** (top right)
3. Click **Help & support**
4. Click **Report a problem**
5. Click **Include** (to include logs and diagnostics — recommended)
6. Fill in the report form (see below)
7. **Capture a screen area** (required — cannot be automated via Playwright)
8. Click **Submit report**

### Report form fields

#### Description (required)

The description must be detailed and factual. Template:

```
I am unable to complete business verification for my Meta Developer app
(Cloudless, App ID: 1936126137016578). When I go to App Review > Verification
and click Start verification for my business portfolio cloudless.gr, I get
a dialog saying "Your account is restricted right now. You have been
temporarily blocked from performing this action."

My personal ad account (ID: 657781691826702) has been disabled since
Jan 24, 2021. Meta says "too much time has passed" and the decision cannot
be reviewed through Account Quality.

However, I need business verification to complete App Review for my
Cloudless app. I have completed all other App Review requirements (app
icon, privacy policy, permissions, screencast, data handling, reviewer
instructions). The only blocker is this verification step.

Please help lift this restriction so I can verify my business and submit
for review.
```

#### Screenshots (recommended)

Attach screenshots that document the issue:
1. The restriction dialog ("Your account is restricted right now")
2. The ad account disabled page (showing "too much time has passed")
3. The App Review submission page (showing disabled Submit button)

Screenshots are stored in `meta-support-screenshots/` (gitignored).

#### Screen capture (required)

The form requires selecting a screen area using the browser's native screen
capture API (`getDisplayMedia`). This **cannot be automated** via Playwright
because:
- It triggers a native browser dialog (not a DOM element)
- The user must manually select a screen or window region
- Playwright cannot interact with native OS-level dialogs

**Workaround**: Click "Capture Screen" and manually select the browser window
or a region showing the restriction. The captured area is attached to the
report automatically.

### Playwright automation notes

The "Report a Problem" form can be partially automated via Playwright MCP:

#### What CAN be automated:
- Navigating to the form (profile > Help & support > Report a problem)
- Clicking "Include" to include logs
- Filling the description textarea (using native React value setter)
- Uploading screenshot files via the file input

#### What CANNOT be automated:
- The "Capture Screen" step (requires native browser screen capture dialog —
  `getDisplayMedia` returns nothing in headless Chromium)

**Verified 2026-10-02: screenshots are OPTIONAL.** The Submit button stays
enabled with details filled even when no media is attached — "Input Details
is valid" appears next to the field and the report submits cleanly (dialog
closes, no error). Reports are fire-and-forget: they do NOT create a case in
Support Inbox. The full flow (profile pic → Help & support → Report a problem
→ Include → fill Details → Submit report) works end-to-end via the FB sidecar
`/debug/eval` — click menuitems with full MouseEvent sequences
(pointerdown→mousedown→pointerup→mouseup→click), and click the last
`[aria-label="Your profile"]` element (not the first).

#### Filling the textarea (React workaround)

Facebook uses React, so setting `textarea.value` directly doesn't trigger
React's state update. Use the native value setter:

```js
const textarea = dialog.querySelector('textarea');
const nativeSetter = Object.getOwnPropertyDescriptor(
  window.HTMLTextAreaElement.prototype, 'value'
).set;
nativeSetter.call(textarea, 'Your report text here...');
textarea.dispatchEvent(new Event('input', {bubbles: true}));
```

#### Uploading screenshots

The file input is inside the dialog but hidden. Trigger it via DOM:

```js
const dialog = document.querySelectorAll('[role="dialog"]')[1];
const fileInput = dialog.querySelector('input[type="file"]');
fileInput.click();
// Then handle the file chooser with Playwright's browser_file_upload
```

#### Dialog visibility workaround

The report dialog may be hidden (not visible to Playwright's accessibility
snapshot). Force it visible:

```js
const dialog = document.querySelectorAll('[role="dialog"]')[1];
dialog.style.display = 'block';
dialog.style.opacity = '1';
dialog.style.visibility = 'visible';
dialog.style.zIndex = '99999';
dialog.style.position = 'fixed';
dialog.style.background = 'white';
dialog.style.color = 'black';
```

### Alternative support channels

If "Report a Problem" doesn't work, try:

| Channel | URL | Notes |
|---------|-----|-------|
| Meta AI Business Assistant | Business Support Home sidebar | Chatbot, cannot create tickets |
| Facebook Help Center | `https://www.facebook.com/help/` | Search for specific issue |
| Developer Support | `https://developers.facebook.com/support/` | For developer-specific issues |
| Bug Reports | `https://developers.facebook.com/support/bugs/` | Platform bugs only |
| Report an incident | `https://developers.facebook.com/incident/report/` | Active incidents |
| Business Help Center | `https://www.facebook.com/business/help/` | Business-specific help |

### Template package

The `template/` directory contains ready-to-use report materials:

| File | Purpose |
|------|---------|
| `template/report-description.txt` | The full report description text (paste into the form) |
| `template/SCREENSHOTS.md` | Manifest of all 14 screenshots with descriptions and source URLs |

Screenshots are stored at `/home/tbaltzakis/cu130-slim/meta-support-screenshots/`
(gitignored). Run `scripts/package-report.py` to bundle everything into a
portable `.tar.gz` archive.

### Scripts

- `scripts/print-report-template.py` — Print the report description template
- `scripts/list-screenshots.py` — List available screenshots for attachment
- `scripts/prepare-screenshots.py` — Capture screenshots via Playwright MCP
- `scripts/package-report.py` — Bundle screenshots + template into a .tar.gz

### Related skills

- `developer-apps-ops` — Diagnose and resolve account restrictions
- `developer-apps-ops` — App Review submission and permission management
- `browser-ops` — Browser automation for Meta pages

## LinkedIn API Developer Access Tier Upgrade

Manage the LinkedIn Marketing API upgrade process from **development tier** to
**standard tier** for the Cloudless SocialAuto platform.

### Context

LinkedIn Marketing API has three access tiers:

1. **Development** — limited to 5 members, 5 pages, 5 ad accounts. Sufficient
   for testing and small-scale use.
2. **Standard** — removes the 5-account limit for POST operations (creating
   ads). No limit on GET/data/analytics calls across any number of accounts.
3. **Direct** — enterprise tier, requires direct LinkedIn partnership.

**Key finding from LinkedIn's email:** The tier upgrade from development to
standard **only affects POST ad creation for more than 5 ad accounts**. There
is no limit on the number of accounts you can call to retrieve
data/analytics. If you are NOT creating ads for more than 5 ad accounts, the
upgrade may not be necessary.

### SocialAuto's LinkedIn API usage

SocialAuto uses the following LinkedIn API products:

#### Share on LinkedIn (`w_member_social`)
- Create organic posts on behalf of authenticated members (personal profiles)
- Create multi-image posts and article posts
- Delete posts

#### Community Management API (`w_organization_social`, `r_organization_social`)
- Create organic posts on behalf of Company Pages (the cloudless.gr carousel pipeline)
- Read organization/page data
- Read post analytics (impressions, clicks, engagement, etc.)
- Read follower counts and lifetime organization stats

#### Organizations API (`r_organization_admin`)
- Discover Company Pages the member administers
- Read organization profile data

#### What SocialAuto does NOT do
- **No LinkedIn Ads creation** — SocialAuto does not use the Marketing API's
  ad campaign, ad account, or sponsored content endpoints
- **No `rw_ads` or `r_ads` scopes** — these are not requested during OAuth
- **No Campaign Manager integration** — no ad campaign creation, editing, or
  optimization

### Upgrade assessment

Given that SocialAuto does not create LinkedIn Ads:

- **If the goal is to manage more than 5 Company Pages** for organic posting
  and analytics → the upgrade IS needed because `w_organization_social` POST
  calls (creating posts as a page) are limited to 5 pages in development tier.
- **If the goal is only to read analytics** from more than 5 pages → the
  upgrade is NOT needed (LinkedIn confirmed no limit on GET/data calls).
- **If the goal is to create ads** for more than 5 ad accounts → the upgrade
  IS needed, but SocialAuto does not currently have ad creation functionality.

### Required submission materials

LinkedIn requires two items for the upgrade review:

#### 1. Business use case description

A written description of:
- What the app offers to customers
- How it leverages the LinkedIn API
- Which specific endpoints/products are used

See `scripts/generate-use-case.py` to generate this automatically from the
codebase, or use the template below.

#### 2. Demo video recording

A screen recording showing:
- The SocialAuto platform UI
- How a user connects their LinkedIn account (OAuth consent flow)
- How a user creates and publishes a post to a LinkedIn Company Page
- How a user views LinkedIn analytics
- (If applicable) How the carousel pipeline generates and publishes content

The video must be shared via Google Drive or Microsoft SharePoint link.

**Demo video script template:**

```
1. Introduction (10s)
   "Hi, I'm Themistoklis from Cloudless. This is a demo of our social media
   automation platform, SocialAuto, which uses the LinkedIn API."

2. Account connection (30s)
   - Show the Accounts page
   - Click "Connect LinkedIn"
   - Show the LinkedIn OAuth consent screen
   - Show the connected account appearing in the list

3. Creating a post (60s)
   - Show the post editor
   - Write a post with text and an image
   - Select the LinkedIn Company Page as the target
   - Click "Publish"
   - Show the post appearing on the LinkedIn Company Page

4. Analytics (30s)
   - Show the Analytics page
   - Show LinkedIn post analytics (impressions, engagement)
   - Show organization-level stats

5. Carousel pipeline (60s) [optional]
   - Show the AI carousel generator
   - Generate a carousel about a topic
   - Show the branded slides
   - Publish to the LinkedIn Company Page

6. Closing (10s)
   "SocialAuto helps businesses manage their LinkedIn presence efficiently.
   Thank you for reviewing our upgrade request."
```

Record with OBS, Loom, or any screen recording tool. Upload to Google Drive
with "anyone with the link can view" permission.

### Reply email template

```
Hi Rajeshwari,

Thank you for the update on our upgrade request.

Business use case:

Cloudless operates SocialAuto, a social media automation platform that helps
businesses manage their presence across LinkedIn, Facebook, Instagram,
Threads, Twitter/X, and TikTok from a single dashboard.

Our LinkedIn integration leverages the following API products:

1. Share on LinkedIn (w_member_social) — allows users to create and publish
   organic posts on their personal LinkedIn profiles, including text posts,
   multi-image posts, and article posts.

2. Community Management API (w_organization_social, r_organization_social) —
   allows users to create and publish organic posts on behalf of LinkedIn
   Company Pages they administer, and read post analytics (impressions,
   clicks, engagement rates) and organization-level statistics (follower
   counts, lifetime analytics).

3. Organizations API (r_organization_admin) — allows us to discover which
   Company Pages a member administers so they can select the correct page
   when publishing.

Our platform does NOT create, edit, or manage LinkedIn Ads or sponsored
content. We do not use the rw_ads or r_ads scopes. Our use case is entirely
organic content publishing and analytics.

We are requesting the standard tier upgrade to support managing more than 5
Company Pages for organic posting, as we work with multiple business clients
who each have their own LinkedIn Company Page.

Demo video:
[INSERT GOOGLE DRIVE LINK HERE]

Please let me know if you need any additional information.

Best regards,
Themistoklis Baltzakis
Cloudless
https://cloudless.gr
```

### Review timeline

- LinkedIn states the review can take up to **14 business days**
- Reply directly to the existing email thread (do not email
  developer-access@linkedin.com separately)
- LinkedIn will follow up with status updates

### Community Management API — `r_member_postAnalytics` (requested 2026-09-25)

`r_member_postAnalytics` (member-post impressions/reactions/comments/shares,
`memberCreatorPostAnalytics` endpoint, API version ≥ 202506) **cannot** be added
to the existing "Cloudless API App" (227354605): CMA must be the *only* product
on an app for legal/security reasons, and that app already has Advertising API +
Share on LinkedIn provisioned. LinkedIn's portal directs you to create a new app.

#### Dedicated app (created & verified 2026-09-25)

- App: **Cloudless Analytics App** — App ID `264925843`, Client ID `77j8kzfi1a6jn8`
- Standalone app, associated with Page `cloudless.gr` (108614163)
- Page association **verified** via Settings → Verify (admin self-serve confirm)
- Products requested: **Community Management API — Development Tier**
- Business email verification: `polar@cloudless.gr` (IMAP on omv-ha; the
  6-digit code arrives in `text/html` — parse both MIME parts, take the
  *freshest* message; several stale codes accumulate)
- Qualtrics access form submitted 2026-09-25 with:
  legal name `Themistoklis Baltzakis`, alternate `Cloudless`,
  website `https://cloudless.gr`, HQ `Koropi, Attica 19400, Greece`
  (matches the Page's declared location), primary use case
  **Direct Advertiser**, secondary: Page management, Page analytics,
  Profile management.
- Status: **pending LinkedIn review** — decision arrives by email.
  Note: once submitted, the Qualtrics link shows "already completed" in the
  same browser; the portal keeps a static "Access request form" link.

#### After approval

1. The new app gets its own OAuth credentials — it is NOT the SocialAuto app.
   `r_member_postAnalytics` tokens must come from THIS app's client id/secret.
2. Decide: add a second LinkedIn OAuth config in `app/api/auth.py` for the
   analytics app (separate client credentials + `r_member_postAnalytics`
   scope), then reconnect the personal LinkedIn account through it.
3. Re-verify `_fetch_member_post_analytics` returns real per-post
   impressions/reactions/comments instead of `member_postAnalytics_scope_missing`.
4. Keep `LINKEDIN_EXTRA_SCOPES` unset on the main app — adding the scope there
   produces `unauthorized_scope_error` (verified 2026-09-25).

### Tool scripts

```bash
## Generate the business use case description from the codebase
.devin/skills/developer-apps-ops/linkedin-api-upgrade/scripts/generate-use-case.py

## Check which LinkedIn scopes are currently configured
.devin/skills/developer-apps-ops/linkedin-api-upgrade/scripts/check-scopes.py
```

#### Status check 2026-09-30

- polar@cloudless.gr IMAP sweep: no decision email yet — CMA access request
  still **pending LinkedIn review** (submitted 2026-09-25; up to 14 business
  days → expected ~mid-October).
- Re-verified via live OAuth probe: the main app (227354605) still rejects
  `r_member_postAnalytics` / `r_member_profileAnalytics` with
  `unauthorized_scope_error` — expected, CMA can never live there.
- `sync_linkedin_account` now short-circuits on the recorded scope list:
  when `r_member_postAnalytics` is provably absent the sync marks posts
  `member_postAnalytics_scope_missing` without issuing the doomed call
  (`_member_post_analytics_scope_missing` in `analytics_sync.py`).
- Once the analytics app (264925843) is approved, follow the "After
  approval" steps above — do NOT touch `LINKEDIN_EXTRA_SCOPES`.

## Twitter/X OAuth Setup

Set up OAuth 2.0 with PKCE for Twitter/X in SocialAuto.
Use when configuring Twitter/X OAuth, creating a developer app at console.x.com,
registering redirect URIs, or debugging Twitter OAuth errors.

### Architecture

Twitter/X uses **OAuth 2.0 Authorization Code Flow with PKCE** (Proof Key for Code Exchange).

| Component | Value |
|-----------|-------|
| Authorize URL | `https://x.com/i/oauth2/authorize` |
| Token URL | `https://api.x.com/2/oauth2/token` |
| User info URL | `https://api.x.com/2/users/me` |
| Auth method | `client_secret_basic` (confidential client) |
| PKCE | Required (`S256`) |

### Required scopes

SocialAuto requests these scopes:

| Scope | Purpose |
|-------|---------|
| `tweet.read` | Read tweets |
| `tweet.write` | Post tweets and retweets |
| `users.read` | Read user profile |
| `media.write` | Upload media via X API v2 `/2/media/upload` (simple + chunked) and alt text `/2/media/metadata` |
| `offline.access` | Get refresh token for long-lived access (access tokens live 2h) |
| `dm.read` / `dm.write` | Unified inbox DMs (needs app permission "Read and write and Direct message") |

Canonical list: `TWITTER_SCOPES` in `social-automation/backend/app/api/auth.py`
(used by every authorize path). Accounts connected before `media.write` was
added must be reconnected; until then media upload is signed with the optional
OAuth 1.0a keys (`TWITTER_API_KEY/SECRET` + `TWITTER_ACCESS_TOKEN/SECRET`).

Optional scopes (add if needed):
- `like.read` / `like.write` — Like/unlike tweets
- `follows.read` / `follows.write` — Follow/unfollow
- `bookmark.read` / `bookmark.write` — Bookmarks
- `list.read` / `list.write` — Lists

Billing: X API is pay-per-use (credits in console.x.com). Post create $0.015,
post with a URL $0.20, media metadata $0.005, user/DM reads billed per
resource. A $0 balance returns HTTP 402 on every billed call (incl.
`/2/users/me` during the OAuth callback) — reconnecting does not help.

### Setup steps

#### 1. Create a developer account

1. Go to https://console.x.com
2. Sign in with your X account
3. Accept the Developer Agreement
4. Complete your profile

#### 2. Create an app

1. Click "New App" (or use an existing one)
2. Enter app name, description, and use case
3. Generate credentials

#### 3. Configure OAuth 2.0

1. In the Developer Console, go to your app > Settings > User authentication settings > Set up
2. Enable **OAuth 2.0**
3. Set **App permissions** to **Read and write and Direct message** (NOT Read-only — `tweet.write` is rejected with "Something went wrong"; plain "Read and write" rejects the `dm.*` scopes)
4. Select **Type of App** = **Web App, Automated App or Bot** (confidential client) to get a Client Secret
5. Set the redirect URI:
   ```
   https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback
   ```
6. Set the Website URL to `https://cloudless.gr`
7. Save settings

#### 4. Save credentials

From the Developer Console > your app > Keys and Tokens:
- Copy the **OAuth 2.0 Client ID**
- Copy the **OAuth 2.0 Client Secret**
- (Optional) Copy the **Bearer Token** for app-only read access

#### 5. Update `.env`

```bash
TWITTER_CLIENT_ID=<your_client_id>
TWITTER_CLIENT_SECRET=<your_client_secret>
TWITTER_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback
## Optional: for app-only read access
TWITTER_BEARER_TOKEN=<your_bearer_token>
```

#### 6. Restart social-api

```bash
cd /home/tbaltzakis/cu130-slim
docker compose restart social-api
curl http://localhost:8083/health
```

#### 7. Connect the account

Open http://localhost:8082/accounts and click **Connect X / Twitter**.

### Token lifecycle

| Token type | Validity | Refresh |
|------------|----------|---------|
| Access token | 2 hours | Use refresh token |
| Refresh token | Until revoked | Requires `offline.access` scope |

#### Refresh token flow

```bash
POST https://api.x.com/2/oauth2/token
Content-Type: application/x-www-form-urlencoded

refresh_token={REFRESH_TOKEN}
&grant_type=refresh_token
&client_id={CLIENT_ID}
```

For confidential clients, include `Authorization: Basic base64(client_id:client_secret)` header.

### PKCE flow

SocialAuto generates PKCE automatically for Twitter:

1. Generate `code_verifier` (random URL-safe string, 43-128 chars)
2. Derive `code_challenge` = base64url(sha256(code_verifier))
3. Send `code_challenge` + `code_challenge_method=S256` in authorize URL
4. Send `code_verifier` in token exchange
5. Twitter verifies the challenge matches

The `code_verifier` is encoded in the OAuth state parameter (base64 JSON) so the callback can use it.

### App settings in Developer Console

| Setting | Value |
|---------|-------|
| App type | Web App, Automated App or Bot (confidential client) |
| App permissions | Read and write and Direct message |
| OAuth 2.0 | Enabled |
| Redirect URI | `https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback` |
| Website URL | `https://cloudless.gr` |
| Scopes | `tweet.read`, `tweet.write`, `users.read`, `media.write`, `offline.access`, `dm.read`, `dm.write` |

### Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `redirect_uri` mismatch | URI not registered in Developer Console | Add the exact HTTPS redirect URI |
| `invalid_grant` | Code already used or expired | Re-authorize (codes expire in 30s) |
| `invalid_client` | Wrong Client ID/Secret | Check `.env` values — Client ID and Secret may be swapped |
| `PKCE verification failed` | Code verifier doesn't match challenge | Ensure PKCE pair is generated correctly |
| 403 Forbidden | App doesn't have access to endpoint | Check app permissions in Developer Console |
| 429 Too Many Requests | Rate limit hit | Check `x-rate-limit-reset` header |
| "Something went wrong / You weren't able to give access to the App" | App permissions set to Read-only while requesting `tweet.write` | Set App permissions to **Read and Write** in User authentication settings |
| Same error (with correct permissions) | Type of App set to Native App (public client) while using a Client Secret | Set Type of App to **Web App, Automated App or Bot** (confidential client) |
| Same error (with correct permissions/type) | Using OAuth 1.0a Consumer Key/Secret instead of OAuth 2.0 Client ID/Secret | Use the **OAuth 2.0 Client ID and Client Secret** from Keys and Tokens, not the Consumer Key/Secret |
| Same error (with all settings correct) | Client ID and Client Secret swapped in `.env` | The Client ID is shorter (`STV6...`); the Client Secret is longer (`ukipam9fP_...`) |

### API endpoints used by SocialAuto

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `https://api.x.com/2/users/me` | GET | Get authenticated user's profile |
| `https://api.x.com/2/tweets` | POST | Create a tweet |
| `https://api.x.com/2/tweets/{id}` | DELETE | Delete a tweet |
| `https://api.x.com/2/users/{id}/tweets` | GET | List user's tweets |

### Scripts

- `scripts/verify-oauth-url.py` — Generate and verify the Twitter OAuth authorize URL

## TikTok Developer Console

Manage the TikTok developer app configuration for SocialAuto's TikTok
integration.

### When to use

- Verify domain ownership for `PULL_FROM_URL` media transfers
- Transfer the Cloudless app from individual to organization ownership
- Submit the app for audit (required for `DIRECT_POST` mode)
- Check app status, scopes, and sandbox vs production mode
- Debug `url_ownership_unverified` errors
- Configure URL properties (domain or URL prefix)

### App details

| Field | Value |
|-------|-------|
| App name | Cloudless |
| App ID | 7630494700880906241 |
| Client key | `TIKTOK_CLIENT_KEY` in `.env` (sbawi6c3634oycojy9) |
| Client secret | `TIKTOK_CLIENT_SECRET` in `.env` |
| Current ownership | Organization `cloudless.gr` |
| Redirect URI | `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback` |
| Products | Login Kit, Content Posting API |
| Mode | **Production — app approved 2026-09-29**; **Direct Post audit: Under review** (submitted 2026-09-29 via `/application/content-posting-api`). Until it clears, `DIRECT_POST` init returns `unaudited_client_can_only_post_to_private_accounts` and the code auto-falls back to `MEDIA_UPLOAD`. |

**Ops skill (scripts + MCP):** `.cursor/skills/tiktok-console-ops/` — domain verify, DNS TXT, sidecar session, console inspect. MCP server key: `tiktok-console` in `.devin/mcp_config.json`.

**Console drift to fix:** if Web URL / Login Kit redirect shows `social.cloudless.jp`, replace with `.gr` SocialAuto paths above.

### Organizations

| Org name | Org ID | Status |
|----------|--------|--------|
| cloudless.gr | 7630331010873377809 | Empty — no apps (target for transfer) |
| cloudless.gr | 7630331010873410577 | Empty — no apps |

Both orgs have the same display name. The app is currently under individual
ownership and needs to be transferred to one of them.

### Transfer app to organization

Per [TikTok docs](https://developers.tiktok.com/doc/working-with-organizations):

1. Go to **Manage apps** at https://developers.tiktok.com
2. Find the **Cloudless** app
3. Click the **three dots (...)** → **Transfer App**
4. Select organization `cloudless.gr` (Org ID: `7630331010873377809`)
5. Click **Initiate Transfer**
6. An email is sent to app administrators to accept the transfer
7. Accept the transfer — **this is irreversible**

After transfer, organization-level features become available, including
URL properties for domain verification.

### Domain verification for PULL_FROM_URL

TikTok's Content Posting API requires that any domain serving media via
`PULL_FROM_URL` be verified. Without verification, the API returns:

```
403 url_ownership_unverified
```

#### Verification steps

Per [TikTok media transfer docs](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/#pull_from_url):

1. Log into https://developers.tiktok.com
2. Open the **Cloudless** app
3. Click **URL properties** button
4. Add a **Domain** property:
   - Enter `cloudless.gr` (base domain covers all subdomains including `social.cloudless.gr`)
   - Or enter `social.cloudless.gr` directly (subdomain only)
5. TikTok generates a DNS verification string (e.g. `tiktok-domain-verification=abc123...`)

> **Sandbox vs Production URL properties are separate.** Verifying the domain on
> the Production tab does NOT cover the Sandbox environment (`cloudless-dev`) —
> unaudited apps' Content Posting calls run in the **sandbox** context, so
> `PULL_FROM_URL` fails with `url_ownership_unverified` until the domain is
> verified on the **Sandbox** tab too. The sandbox flow issues a
> `tiktok-developers-site-verification=...` TXT record (not
> `tiktok-domain-verification=`) — add it as a TXT on `@` anyway; it verifies
> the domain for sandbox URL properties.

#### Add DNS TXT record in Cloudflare

1. Log into https://dash.cloudflare.com → select `cloudless.gr`
2. **DNS** → **Records** → **Add record**
3. Set:
   - Type: `TXT`
   - Name: `@` (for base domain) or `social` (for subdomain)
   - Content: the verification string from TikTok
4. Save — Cloudflare propagates within seconds

#### Complete verification

1. Back in TikTok developer console, click **Verify**
2. TikTok checks the DNS record — should pass within a minute
3. All URLs under the verified domain are now trusted for `PULL_FROM_URL`

#### Verification rules

- **Domain** verification covers all paths under that domain AND its subdomains
  - Verifying `cloudless.gr` covers `social.cloudless.gr`, `www.cloudless.gr`, etc.
- **URL Prefix** verification covers only URLs with the exact prefix
  - `https://example.com/videos/user/` covers `.../user/123.mp4` but not `.../2023/user/123.mp4`
- The media URL must use `https` and must not redirect (no 3xx)
- The URL must remain accessible for the entire download duration (1h timeout)
- Domain verification is recommended over URL prefix (broader coverage)

#### Check if verification is needed

Only apps created after TikTok's enforcement date require URL verification.
Older apps may be grandfathered. Check by attempting a `PULL_FROM_URL` call —
if it returns `url_ownership_unverified`, verification is required.

### Workaround: FILE_UPLOAD (no verification needed)

For **video** posts, SocialAuto supports `FILE_UPLOAD` which reads the local
video file and uploads bytes directly to TikTok. This bypasses domain
verification entirely. See the `tiktok-console-ops` skill for details.

For **photo** posts, `PULL_FROM_URL` is the only option — domain verification
is mandatory.

### App audit for DIRECT_POST

`DIRECT_POST` mode (posting directly to the profile without creator approval)
requires the app to pass TikTok's audit review.

#### Submit for audit

The app-level review does **not** enable public Direct Post — that needs the
separate Content Posting API audit:

1. Open the **Cloudless** app in the developer console → Content Posting API
2. Click the **Apply** link beside Direct Post →
   `https://developers.tiktok.com/application/content-posting-api`
3. Complete the 4-step wizard (org info, App ID, goal, daily-user estimate,
   MP4 screen recording, DB-fields list, 3 declaration checkboxes) — see
   `.devin/skills/tiktok-console-ops/scripts/cp-audit-application.py`
4. TikTok reviews it (several business days); the console shows **"Under
   review"** beside Direct Post until it clears

#### Audit status

**Under review — submitted 2026-09-29.** Two separate approvals apply:

1. **App-level review** (approved 2026-09-29): lifted sandbox mode, enabled
   production OAuth, and granted `video.publish`/`video.upload` scopes.
   `creator_info` returns `PUBLIC_TO_EVERYONE`.
2. **Direct Post audit** (separate, required): submitted via the "Apply"
   link beside the Direct Post toggle →
   `https://developers.tiktok.com/application/content-posting-api`. The
   4-step wizard needs org info, App ID, goal description, a daily
   publishing-user estimate (submitted "Less than 100"), an MP4 screen
   recording of the OAuth→compose→post UX (`docs/tiktok-demo/videos/
   tiktok-demo.mp4`), the DB-fields list, and 3 declaration checkboxes.
   Console shows **"Under review"** beside Direct Post until it clears.

While the audit is pending, `DIRECT_POST` init returns
`403 unaudited_client_can_only_post_to_private_accounts`; SocialAuto's
publish code attempts `DIRECT_POST` first and automatically retries as
`MEDIA_UPLOAD` on that error, so posts still land as inbox drafts.

**When the audit approves:** a `SELF_ONLY` init succeeding is *not* proof —
unaudited clients can already init private posts. Verify the console shows
the audit as approved (the Apply link is gone / status flips), then run a
`DIRECT_POST` init with `PUBLIC_TO_EVERYONE` — only a `publish_id` from that
confirms the restriction lifted. Keep `MEDIA_UPLOAD` as the per-post
native-editor option either way.

### Sandbox mode (historical)

The app was in Sandbox mode before approval — only listed sandbox users
(`cloudless-dev` / `user3113682023385`) could authorize. Production
approval removed that restriction; any TikTok user can authorize now.

### TikTok OAuth specifics

TikTok Login Kit has several non-standard OAuth requirements:

- **`client_key`** (not `client_id`): Used in authorize URL and token exchange
- **PKCE required**: `code_challenge` + `code_challenge_method=S256` always
- **Comma-separated scopes**: `user.info.basic,video.publish,video.upload` (not space-separated)
- **HTTPS-only redirect URIs**: `TIKTOK_REDIRECT_URI` must use `https://`
- **24-hour tokens**: No `expires_in` on refresh — 24h assumed

The custom `TikTokOAuth2` class in `app/api/auth.py` handles all of these.

#### Required scopes

| Scope | Purpose |
|-------|---------|
| `user.info.basic` | Read user profile info |
| `video.publish` | Publish content to TikTok |
| `video.upload` | Upload videos via Content Posting API |

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## Check TikTok app configuration from .env
.devin/skills/developer-apps-ops/tiktok-dev-console/scripts/check-app-config.py

## Verify that social.cloudless.gr is reachable and serving media
.devin/skills/developer-apps-ops/tiktok-dev-console/scripts/verify-media-url.py

## Fetch the latest TikTok Content Posting API docs
.devin/skills/developer-apps-ops/tiktok-dev-console/scripts/fetch-docs.py
```

### Important notes

- Domain verification is a **one-time** setup per domain
- The app transfer to an organization is **irreversible**
- Sandbox mode restricts access to listed users only
- `MEDIA_UPLOAD` sends to inbox; `DIRECT_POST` publishes directly (needs audit)
- Never commit `TIKTOK_CLIENT_SECRET` or access tokens
- The TikTok developer console cannot be automated via API — all console
  operations (domain verification, app transfer, audit submission) are manual
