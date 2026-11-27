# Meta Services — Status & Unresolvable Issues

**Last updated:** 2026-11-27
**Meta App ID:** 1936126137016578
**Instagram App ID:** 1462851805249610
**Public domain:** social.cloudless.gr
**Business Manager:** `cloudless.gr` (1558125105019725) — **verification IN REVIEW** (submitted 2026-11-27, ~2 business days)

---

## Service Inventory

| # | Service | API | Status | Path Used |
|---|---------|-----|--------|----------|
| 1 | Instagram (Business Login) | graph.instagram.com v26.0 | ✅ Working | OAuth → token exchange → profile → publish |
| 2 | Instagram (Facebook Login) | graph.facebook.com v26.0 | ❌ Broken (error #10) | Page-Instagram linkage missing |
| 3 | Instagram (Private API / instagrapi) | Mobile private API | ❌ Broken | Login fails: "version out of date" |
| 4 | Instagram (Web API / rupload_igphoto) | i.instagram.com | ⚠️ Fragile | Single image works, carousel 500s, session expires |
| 5 | Facebook Page | graph.facebook.com v26.0 | ✅ Working | Page token, posting, messaging |
| 6 | Facebook User | graph.facebook.com v26.0 | ✅ Working | User token, page discovery |
| 7 | Messenger (Page) | graph.facebook.com v26.0 | ✅ Working | Send API, webhook, AI auto-reply |
| 8 | WhatsApp Cloud API | graph.facebook.com v26.0 | ✅ Working | User token re-authed 2026-11-27; business profile set |
| 9 | Meta App Review | developers.facebook.com | ⏳ Not submitted | Standard Access only (admin-only) |
| 10 | Meta Rate Limits | developers.facebook.com | ✅ OK (~5% used) | Monitoring only, not configurable |
| 11 | BM Verification | business.facebook.com | ⏳ In review | Submitted 2026-11-27 — unblocks system-user mint (#100) |
| 12 | Meta OAuth + 2FA | sidecar/CDP drive | ✅ Automated | TOTP self-serve (META_TOTP_SECRET*), zero human clicks |

---

## ✅ Working Services

### 1. Instagram Business Login (graph.instagram.com)

**Account:** `@cloudless.gr` (BUSINESS, 12 media, 2 followers)
**Account ID in SocialAuto:** `38ddbd44-8811-4d0b-be62-a23fd2f50490`
**IG User ID:** `28747382798219804`
**Login type:** `business_login`

**What works:**
- OAuth consent flow (4 permissions: basic, content_publish, manage_comments, manage_messages)
- Short-lived → long-lived token exchange (POST to graph.instagram.com/access_token)
- Profile fetch (username, account_type, media_count, followers_count)
- Single image publishing (POST /media → POST /media_publish)
- Carousel publishing (POST /media with is_carousel_item → POST /media with media_type=CAROUSEL → wait → POST /media_publish)
- Token refresh (GET /refresh_access_token with grant_type=ig_refresh_token)

**What was fixed:**
1. Added "Instagram" product with "API setup with Instagram login" to the Meta app
2. Configured Business Login redirect URI: `https://social.cloudless.gr/api/v1/auth/oauth/instagram2/callback`
3. Corrected Instagram App ID in `.env` (1462851805249610, not the Meta app ID)
4. Accepted Instagram Tester invite for @cloudless.gr
5. Fixed callback routing (generic /oauth/{platform}/callback was shadowing /oauth/instagram2/callback)
6. Fixed long-lived token exchange from GET to POST (Meta API change in 2025)

**Limitations (Standard Access, no App Review):**
- Only works for app admins/developers/testers
- Cannot be used by external users without App Review
- Token expires in 60 days (refreshable via /refresh_access_token)

### 2. Facebook Page

**Page:** Cloudless.gr (ID: 1163886186808102)
**Account ID in SocialAuto:** `e280d96f-2807-4150-babe-f388d02bcfba`

**What works:**
- Page posting (text, link, image)
- Page info read (name, fan_count)
- Messenger Send API
- Webhook for incoming messages

**Permissions granted:** `pages_show_list`, `pages_messaging`, `read_insights`, `ads_management`, `ads_read`, `business_management`, `instagram_basic`, `instagram_manage_insights`

### 3. Facebook User

**User:** Themistoklis Baltzakis (ID: 10239451610085137)
**Account ID in SocialAuto:** `9355ed63-7787-43e5-a22d-ae0a33d5176b`

**What works:**
- User info read
- Page discovery (GET /me/accounts)
- Business Manager discovery (GET /me/businesses)
- Token source for Page tokens

### 4. Messenger (Page)

**What works:**
- Send API (text, templates, media)
- Webhook for incoming messages
- AI auto-reply (Cloudflare Workers AI)
- Greeting text, ice breakers, persistent menu
- 24-hour messaging window compliance

---

## ❌ Unresolvable Issues

### Issue 1: Instagram Facebook Login — Error #10 (Page-Instagram Linkage Missing)

**Status:** PARTIALLY RESOLVED (2026-11-27) — app-side link confirmed + propagated to `instagram_accounts`; `instagram_business_account` propagation pending (Meta lag, recheck ~48h)
**Workaround:** Use Business Login instead (working)

**Description:**
The Instagram account `@cloudless.gr` is NOT linked to any Facebook Page at the Graph API level. Both Facebook Pages (`Cloudless.gr` and `cloudless.gr`) return no `instagram_business_account` field. The Meta Business Suite UI shows the Instagram account linked, but the Graph API does not see the link.

**What was tried:**
- `GET /{page_id}?fields=instagram_business_account` → empty (still empty 2026-11-27)
- `GET /{page_id}/instagram_accounts` → **NOW returns `17841436821573754`** (was empty — propagated after the IG-app "Confirm Connection" on 2026-11-27)
- **2026-11-27 fix drive part 2**: `instagram_reauth.py` (unattended FB-sidecar OAuth) reactivated `fb_onboarding` row `d2215106` → **active** (token type=PAGE, carries `instagram_content_publish`; publish still #10 until IBA propagates — watcher `ig_page_link` added to call_audit api lane)
- **2026-11-27 fix drive**: IG app → cloudless.gr → Edit profile → Facebook → Review connection → Confirm Connection → m.facebook.com CustomTab login. FB identity = **phone `306977777838` + `FACEBOOK_PASSWORD`** (gmail creds log into a no-profile Meta Account; hotmail creds stale). 2FA = "Notification on another device" → approve in the logged-in FB app on the same phone (push lands in FB notifications; WhatsApp-code lane unusable — WA app itself mid-verification). Page token now READS the IG node (`followers_count:17, username:cloudless.gr`); POST /media still #10 until `instagram_business_account` propagates.
- `GET /{page_id}/page_backed_instagram_accounts` → empty
- `POST /{page_id}/page_backed_instagram_accounts` → permission denied
- Business Manager `owned_instagram_accounts` → empty
- Business Manager `2432271077303446` → token lacks `VIEW_INSTAGRAM_ACCOUNTS`

**Root cause:**
The Instagram account is in a different Business Manager (`2432271077303446`) than the one the token can access (`1558125105019725`). The Page-Instagram linkage was done via Meta Business Suite, which doesn't always propagate to the Graph API (known Meta bug, documented on Stack Overflow).

**Why it can't be resolved:**
- Linking an Instagram account to a Facebook Page via the Graph API requires Business Manager access that the current token doesn't have
- The `page_backed_instagram_accounts` edge requires `INSTAGRAM_MANAGE_INSIGHTS` with Advanced Access (App Review)
- The Meta Business Suite UI linkage doesn't propagate to the Graph API
- This is a known Meta platform bug with no API-side fix

**Impact:** None — Business Login path (graph.instagram.com) is used instead and works correctly.

---

### Issue 2: Instagram Private API (instagrapi) — Login Broken

**Status:** CANNOT BE RESOLVED without instagrapi upstream fix — **CONFIRMED AGAIN 2026-11-27 at latest versions**
**Version installed:** 3.2.22 (social-api) / aiograpi 2.1.22 (instagram-private-api sidecar)

**2026-11-27 re-verification:**
- instagrapi 3.2.19→3.2.22 hot-patch + `ClientNeedsUpgradeError` persists → block is credential/IP-level at IG's edge, NOT library-version (matches the 2026-09-28 dead-end note)
- sidecar rebuilt from local `instagram-private-api/Dockerfile` (was running raw upstream `subzeroid/aiograpi-rest` v6.0.0/aiograpi 1.0.9 — the override had never applied); now aiograpi 2.1.22 → same `ClientNeedsUpgradeError` on `/auth/login`
- ALL 8 stored sessions in `/data/db.json` + `ig_session.json` → 401 `ClientUnauthorizedError` (dead)
- Sidecar recreated clean; stale hash-prefixed duplicate removed

**Description:**
The `instagrapi` library (unofficial Instagram private mobile API) can no longer log in to Instagram. All login attempts fail with:
- "Your version of Instagram is out of date. Please update your account in the Instagram app."
- ProxyError when using WARP proxy

**What was tried:**
- `cl.login(username, password)` → "version out of date"
- `cl.login_by_sessionid(session_id)` → succeeds, but `album_upload()` fails with LoginRequired
- `cl.login_legacy()` → ProxyError
- Updated instagrapi to latest (2.18.20) → still fails
- Tried with and without WARP proxy → both fail

**Root cause:**
Instagram changed their private mobile API login flow. The instagrapi library has not been updated to handle the new login mechanism. This is an upstream library issue, not a configuration issue.

**Why it can't be resolved:**
- The fix requires an update to the instagrapi library itself
- Instagram actively blocks private API access from non-official clients
- Even when login succeeds (via sessionid), media upload fails with LoginRequired
- The aiograpi-rest sidecar (v2.0.2) has the same issue

**Impact:** Low — the Business Login Graph API path handles all publishing needs. The private API was only a fallback for features the Graph API doesn't support (e.g., Stories, direct messaging from non-business accounts).

---

### Issue 3: Instagram Web API (rupload_igphoto) — Fragile

**Status:** PARTIALLY WORKING but unreliable

**Description:**
The Instagram Web API (used by instagram.com in the browser) can publish single images via `rupload_igphoto` + `/api/v1/media/configure/`, but:
- Single image: ✅ Works (posted to https://www.instagram.com/p/DdO4VL7iGE-/)
- Carousel (configure_sidecar): ❌ HTTP 500 "Unknown Server Error" — even with correct children_metadata format (width, height, extra, edits, device)
- Sessions expire quickly after API use
- Instagram blocks API calls from server IPs (even with cookies)

**What was tried:**
- Extracted cookies from browser session → single image posted successfully
- Carousel with correct children_metadata (extra, edits, device as JSON strings) → 500
- Browser bridge fetch() from within browser context → session expired
- Re-extracted fresh cookies → session invalidated after single post

**Root cause:**
Instagram's Web API is not a public API. It's the internal API used by instagram.com. Instagram actively detects and blocks server-side use. Carousel publishing via the Web API uses a different endpoint (`configure_sidecar`) that has additional anti-automation checks.

**Why it can't be resolved:**
- Instagram continuously updates anti-automation measures
- Carousel publishing via Web API requires device fingerprinting that's hard to replicate
- Sessions are invalidated after suspicious activity
- This is an ongoing cat-and-mouse game with no stable solution

**Impact:** Low — the Business Login Graph API path handles carousel publishing correctly.

---

### Issue 4: WhatsApp Cloud API — RESOLVED (2026-11-10)

**Status:** LIVE — direct Cloud API registration complete
**WABA / Messaging account ID:** 1717901383017723 (WAAC 1805020980543501, "Cloudless")
**Phone Number ID:** 1432734783254091
**Display Phone:** +30 697 777 7838
**Meta app:** 1936126137016578 ("Cloudless", namespace cloudless_gr)

**Current state (verified via Graph):**
- `code_verification_status`: `VERIFIED`
- `messaging_limit_tier`: `TIER_250`
- `name_status`: `AVAILABLE_WITHOUT_REVIEW` (verified_name "Cloudless")
- `quality_rating`: `UNKNOWN`
- App subscribed to WABA webhooks (`subscribed_apps` returns app 1936126137016578)
- Webhook callback `https://social.cloudless.gr/api/v1/whatsapp/webhook`
  challenge-verified; `X-Hub-Signature-256` enforced via `FACEBOOK_APP_SECRET`
  (falls back to `FACEBOOK_CLIENT_SECRET` in compose)
- `social_accounts` row 77f17091-3639-4633-b04d-0a3345dd7d3a: status=active,
  CLOUD_API, new IDs, encrypted token, bot config preserved,
  `token_expires_at` = 2027-02-06 (user-token data-access expiry; the
  `token_expiry` sweep warns 14d ahead and escalates to Slack)

**History:** the original WABA 1073707258453499 / phone_number_id
1334613883061552 belonged to a different (partner-owned) portfolio and
remains orphaned. The WA Business app account on +30 697 777 7838 was
deleted (chat backup taken first) so the number could be verified via SMS
and registered directly for Cloud API — coexistence was not possible
because the Meta app is not a Tech Provider/Solution Partner.

**Remaining:**
- Message templates must be approved on the new WABA before
  business-initiated sends (24h session replies work now). Template
  status events arrive via webhook (`message_template_status_update`).
- WHATSAPP_ACCESS_TOKEN is a long-lived USER token (data access expires
  ~2027-02); mint a system-user token in Business Settings when possible
  for a non-expiring credential.
- First real inbound message still needs a live end-to-end check
  (webhook → unified inbox → auto-reply bot).

**Quota guards (2026-10-06, `whatsapp_verify.py`):** the 30-min verification
beat is gated so it can't burn Meta's 10-requests-per-72h budget:

- Every `request_code` attempt is logged per phone in
  `meta_data.whatsapp_code_request_log` (pruned to 72h); ≥9 logged attempts
  skips the request — one slot of headroom under the cap.
- A `136024` rejection stamps `whatsapp_rate_limited_at` +
  `whatsapp_rate_limited_phone_id` and gates for 72h, scoped to that phone.
- A successful send stamps `whatsapp_code_sent_at`/`_phone_id`; resends are
  suppressed for 24h while the status GET keeps polling.
- `update_whatsapp_credentials` clears all cooldown keys when
  `phone_number_id` changes (quotas are per-number).

State table: `.devin/skills/whatsapp-ops/SKILL.md` §Meta rate limits.

---

### Issue 4b: Business Manager Verification — IN REVIEW (2026-11-27)

**Status:** SUBMITTED — `verification_status: pending` (was `pending_need_more_info`)
**Portfolio:** `cloudless.gr` BM `1558125105019725`
**Review ETA:** ~2 business days from 2026-11-27

**Why it matters:** the #100 system-user capability gate (WhatsApp/ad
provisioning via `meta_infra_retry` beat, error subcode 2859015) clears once
the BM is verified. The daily 10:00 beat re-runs the full provisioning chain
automatically — no human step needed on approval.

**Submission (driven live via playwright MCP persistent profile):**
- Business type: Sole Proprietorship → "Not yet registered"
- Legal name: `ΜΠΑΛΤΖΑΚΗΣ ΘΕΜΙΣΤΟΚΛΗΣ` — **Greek, matching docs byte-for-byte**
  (Meta's doc auto-check does literal text detection; a Latin name entry
  fails against Greek-language documents)
- Address: `ΘΕΡΜΟΠΥΛΩΝ 15, 19400 ΚΟΡΩΠΙ` (matches EFKA/tax + phone bill)
- Name doc: NBG VISA statement as *Business Bank Statement*
- Address/phone doc: Telekom mobile bill as *Utility Bill*
  (name + `194 00 ΚΟΡΩΠΙ` + line 6977777838 matching the entered number)
- Connection: Domain verification — cloudless.gr already a verified domain
- Canonical docs: `Documents\mpaltzakis-correspondence\οικονομικά-αποδεικτικά-ΑΦΙ\`

**Watcher:** `call_audit.py` api lane polls `verification_status` daily
(09:15 `CallSurfacesAudit`); PASS on `pending`/`verified`, FAIL → Telegram
ops alert on `pending_need_more_info`/`rejected`.

**Full wizard playbook** (reCAPTCHA Enterprise pass, doc-type dropdowns,
setInputFiles upload, stale-attachment gotcha): `meta-oauth-drive` SKILL.md
"Business verification wizard" section.

**If rejected again:** likely needs a doc where legal name = the portfolio
name, or a second admin + passkey per Security Center's own recommendations.

---

### Issue 5: Meta App Review — Not Submitted

**Status:** REQUIRES MANUAL SUBMISSION (cannot be automated)
**Submission URL:** https://developers.facebook.com/apps/1936126137016578/app-review/submissions/

**Description:**
The Meta app is in Live mode but has NOT completed App Review for Advanced Access. All Instagram Business Login permissions are in "Ready for testing" status, which means:
- ✅ Works for app admins, developers, and testers
- ❌ Does NOT work for external/regular users
- ❌ Cannot be used in production for multi-tenant use

**Permissions needing App Review:**
| Permission | Current Access | Needed For |
|-----------|---------------|------------|
| `instagram_business_basic` | Standard | Profile read for any user |
| `instagram_business_content_publish` | Standard | Publishing for any user |
| `instagram_business_manage_comments` | Standard | Comment management for any user |
| `instagram_business_manage_messages` | Standard | DM management for any user |
| `pages_messaging` | Standard | Messenger for any Page |
| `whatsapp_business_messaging` | Standard | WhatsApp for any number |
| Human Agent | Standard | Automated replies with 7-day window |

**Why it can't be automated:**
- App Review requires screencast videos showing how the app uses each permission
- Requires business verification — **IN REVIEW since 2026-11-27** (Issue 4b)
- Requires privacy policy and terms of service URLs
- Review takes 5-14 business days
- Meta reviewers manually test the app

**Impact:** For the current single-user (admin) setup, there is NO impact — Standard Access is sufficient. App Review is only needed if:
- External users connect their own Instagram/Facebook accounts
- The app is offered as a SaaS to multiple businesses
- The app is listed publicly

**Guide:** See `docs/meta-app-review-submissions.md` for the complete submission guide with screencast scripts and permission descriptions.

---

### Issue 6: Meta Rate Limits — Not Configurable

**Status:** N/A (monitoring only)

**Description:**
The Meta app's rate-limit page shows approximately 5% usage with 95% remaining. The rate limit is application-level (~200 calls per hour per user, aggregated). There is NO configurable rate-limit setting in the Meta dashboard — it only shows usage monitoring.

**Why it can't be changed:**
- Rate limits are set by Meta based on app type and usage history
- The only way to increase limits is through App Review (which grants Advanced Access)
- There is no "rate limit configuration" in the dashboard

**Impact:** None at current usage levels. The SocialAuto app has built-in adaptive rate limiting (Redis-based) that throttles requests before hitting Meta's limits.

---

### Issue 7: reCAPTCHA / CAPTCHA — Not Applicable

**Status:** N/A

**Description:**
No reCAPTCHA or CAPTCHA configuration was found in the Meta Developer dashboard. Meta does not expose a CAPTCHA configuration for API apps. CAPTCHA challenges may appear during:
- Manual browser-based OAuth flows (handled by the user in the browser)
- Instagram Web API access (anti-bot detection)
- Facebook login from new IP addresses

**Why it can't be configured:**
- Meta does not offer a CAPTCHA configuration for developer apps
- CAPTCHA challenges are triggered by Meta's risk engine, not by app settings
- There is no way to disable or configure CAPTCHA from the developer dashboard

**Impact:** None for API-based flows (Business Login, Graph API). CAPTCHA only affects manual browser interactions, which are handled by the user.

---

## Architecture: Instagram Publishing Fallback Chain

SocialAuto attempts Instagram publishing through 5 paths in priority order:

```
1. Business Login Graph API (graph.instagram.com)     ← ACTIVE, WORKING
   ↓ (fallback if business_login token missing)
2. instagrapi (private mobile API)                     ← BROKEN (upstream issue)
   ↓ (fallback if no credentials)
3. Web API (rupload_igphoto)                           ← FRAGILE (session expires)
   ↓ (fallback if no web session)
4. aiograpi-rest sidecar                               ← BROKEN (same login issue)
   ↓ (fallback if no sidecar session)
5. Facebook Login Graph API (graph.facebook.com)       ← BROKEN (error #10, no Page linkage)
```

**Current active path:** #1 (Business Login Graph API) — handles single images and carousels.

---

## Token Refresh Schedule

| Token Type | Validity | Refresh Method | Automated? |
|-----------|----------|----------------|------------|
| Instagram Business Login (long-lived) | 60 days | GET /refresh_access_token | ✅ Celery beat (daily check) |
| Facebook User (long-lived) | 60 days | GET /oauth/access_token (fb_exchange_token) | ✅ Celery beat |
| Facebook Page | 60 days (derived from user token) | Re-fetch from user token | ✅ Celery beat |
| WhatsApp (System User) | Does not expire | N/A | N/A |

---

## Environment Variables

All Meta-related environment variables in `/home/tbaltzakis/cu130-slim/.env`:

| Variable | Purpose | Status |
|----------|---------|--------|
| `FACEBOOK_CLIENT_ID` | Meta app ID (Facebook Login) | Set (1936126137016578) |
| `FACEBOOK_CLIENT_SECRET` | Meta app secret | Set |
| `FACEBOOK_REDIRECT_URI` | Facebook OAuth callback | Set (social.cloudless.gr) |
| `INSTAGRAM2_CLIENT_ID` | Instagram App ID (Business Login) | Set (1462851805249610) |
| `INSTAGRAM2_CLIENT_SECRET` | Instagram App secret | Set |
| `INSTAGRAM2_REDIRECT_URI` | Instagram Business Login callback | Set (social.cloudless.gr) |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp phone number ID | Set (1432734783254091) |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | WABA / messaging account ID | Set (1717901383017723) |
| `WHATSAPP_APP_ID` | Meta app for WhatsApp webhooks | Set (1936126137016578) |
| `WHATSAPP_ACCESS_TOKEN` | Cloud API bearer token (user token, data access ~2027-02) | Set |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification token | Set |

---

## Action Items

| # | Action | Type | Blocks? |
|---|--------|------|---------|
| 1 | ~~Approve WhatsApp message templates on WABA 1717901383017723~~ | **DONE 2026-11-27 — 4 templates APPROVED** (`cloudless_alert`×2 MARKETING, `cloudless_followup`×2 UTILITY) | Business-initiated sends |
| 2 | BM verification review completes | Meta review (~2bd from 11-27) | System-user mint, #100 gate |
| 3 | Mint system-user token for WHATSAPP_ACCESS_TOKEN | Automated (meta-infra-retry beat post-verification) | Token expiry ~2027-02 |
| 4 | Submit Meta App Review (if multi-tenant needed) | Manual (dashboard) | External user onboarding |
| 5 | Update instagrapi when upstream fixes login | Dependency | Private API fallback |
| 6 | Monitor Meta rate limits | Automated (dashboard) | N/A |

---

## Related Documentation

- `docs/api-integration-audit.md` — Full API endpoint audit across all platforms
- `docs/application-architecture.md` — System architecture and platform capabilities
- `docs/meta-app-review-submissions.md` — App Review submission guide with screencast scripts
- `docs/messenger-architecture.md` — Messenger bot architecture
- `docs/messenger-bot-guide.md` — Messenger bot configuration guide
- `docs/messenger-app-review-permissions.md` — Messenger App Review permissions
- `.devin/skills/developer-apps-ops/SKILL.md` — Meta App Review skill
- `.devin/skills/developer-apps-ops/SKILL.md` — Meta OAuth setup skill
- `.devin/skills/instagram-ops/SKILL.md` — Instagram private API skill
- `.devin/skills/whatsapp-ops/SKILL.md` — WhatsApp phone verification skill
