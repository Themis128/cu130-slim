# SocialAuto SaaS Readiness Plan

> **Artifact type:** Plan
> **Created:** 2026-09-07
> **Status:** Draft — awaiting approval
> **Owner:** Themistoklis Baltzakis
> **Repo:** `cu130-slim` (Docker Compose stack)

## Purpose

Address the gaps preventing SocialAuto from operating as a full
self-service SaaS platform. The core engine (AI generation, publishing,
analytics, media, SEO, 6 platforms, Cloudflare-first infrastructure) is
production-grade. This plan covers the missing **business layer** and
**operational reliability** items.

## Current state summary

| Area | Status |
|------|--------|
| 29 Docker containers | All healthy |
| API (259 endpoints, 223 paths) | Healthy |
| Unit tests | 427 passed, 1 skipped |
| Lint (ruff) | Clean |
| 3 Celery workers | All online |
| Cloudflare D1/KV/Vectorize | All true, dual-write active |
| Frontend (Next.js App Router, 40+ pages) | HTTP 200 |
| n8n (9 workflows) | Running |
| Auth: register, login, password reset, 2FA, OAuth | Present |
| Rate limiter (slowapi + Redis) | Present, IP-based, 300/min default |
| Email digest service (Resend SMTP) | Present, digest-only |
| Team/Role model (Owner/Admin/Editor/Viewer) | Present in DB, no management API |
| API docs (Swagger/ReDoc) | Disabled in production (`DEBUG=true` in `.env` so currently visible) |
| Users in DB | 1 |
| Teams in DB | 1 |

---

## Phase 1 — Self-Service Onboarding (blocks new signups)

### 1.1 Multi-tenant isolation test

**Problem:** Only 1 user, 1 team in the database. Team-scoped data
isolation is unproven. A second tenant could see or overwrite another
tenant's data.

**Current state:**
- `User`, `Team`, `TeamMember` models exist with `team_id` foreign keys
  on all content tables (`posts`, `media_assets`, `social_accounts`,
  `brands`, `ai_usage_logs`, etc.).
- `register()` endpoint creates a default team + OWNER membership
  automatically.
- `get_current_team_id` / `get_current_team` deps resolve the active
  team from the JWT.
- `require_team_role(min_role)` dependency exists for role-based access.
- **Missing:** No API to list teams, switch active team, invite members,
  accept invitations, or remove members.

**Tasks:**

1. **Create team management API** (`app/api/teams.py`):
   - `GET /api/v1/teams` — list teams the current user belongs to
   - `POST /api/v1/teams` — create a new team
   - `GET /api/v1/teams/{team_id}` — get team details + members
   - `PATCH /api/v1/teams/{team_id}` — update team name
   - `DELETE /api/v1/teams/{team_id}` — delete team (OWNER only)
   - `POST /api/v1/teams/{team_id}/invite` — invite by email
   - `POST /api/v1/teams/{team_id}/members/{user_id}` — add member
   - `PATCH /api/v1/teams/{team_id}/members/{user_id}` — change role
   - `DELETE /api/v1/teams/{team_id}/members/{user_id}` — remove member
   - `POST /api/v1/teams/{team_id}/switch` — set active team in JWT

2. **Add team switch to JWT**: Extend the login/refresh flow to include
   `active_team_id` in the token claims. Add a `/auth/switch-team`
   endpoint that issues a new token with a different active team.

3. **Write integration tests** for multi-tenant isolation:
   - Create two users, two teams
   - Verify user A cannot read user B's posts, media, accounts
   - Verify user A cannot publish to user B's social accounts
   - Verify admin can invite a viewer and viewer cannot edit
   - Verify team deletion cascades correctly

4. **Frontend team switcher**: Add a team dropdown in the Header
   component (next to user avatar) that calls `switch-team` and
   refreshes the session.

**Files to create:**
- `social-automation/backend/app/api/teams.py`
- `social-automation/backend/tests/unit/test_teams.py`
- `social-automation/backend/tests/integration/test_multi_tenant.py`

**Files to modify:**
- `social-automation/backend/app/api/__init__.py` (add teams router)
- `social-automation/backend/app/api/auth.py` (switch-team endpoint)
- `social-automation/frontend/app/(dashboard)/settings/page.tsx` (team management UI)
- `social-automation/frontend/components/layout/Header.tsx` (team switcher)

**Verification:**
- `pytest tests/unit/test_teams.py -q` passes
- `pytest tests/integration/test_multi_tenant.py -q` passes
- Manual: register a second user, verify they can't see the first
  user's data

---

### 1.2 Public landing + pricing page

**Problem:** `social.cloudless.gr` root (`app/page.tsx`) immediately
redirects to `/login` or `/dashboard`. There is no marketing page,
no feature showcase, no pricing table, no "Get Started" CTA. The SaaS
has no storefront.

**Current state:**
- Next.js App Router with 40+ dashboard pages
- No `(public)` route group
- No marketing components (hero, features, pricing, testimonials)
- The root `app/page.tsx` is a 20-line redirect stub

**Tasks:**

1. **Create a `(public)` route group** with its own layout:
   - `app/(public)/layout.tsx` — public navbar + footer
   - `app/(public)/page.tsx` — landing page (hero, features, CTA)
   - `app/(public)/pricing/page.tsx` — pricing tiers table
   - `app/(public)/features/page.tsx` — feature deep-dive
   - `app/(public)/about/page.tsx` — company info

2. **Update root `app/page.tsx`** to show the landing page for
   unauthenticated visitors, redirect to `/dashboard` for authenticated
   users (instead of always redirecting).

3. **Create reusable marketing components:**
   - `components/marketing/Hero.tsx`
   - `components/marketing/FeatureGrid.tsx`
   - `components/marketing/PricingTable.tsx`
   - `components/marketing/CTASection.tsx`
   - `components/marketing/PublicNav.tsx`
   - `components/marketing/PublicFooter.tsx`

4. **Pricing tiers** (placeholder until billing is built):
   - Free: 1 social account, 10 posts/month, basic AI
   - Pro: 5 social accounts, 100 posts/month, advanced AI, analytics
   - Business: 20 social accounts, unlimited posts, team members, brand kit
   - Enterprise: custom, SSO, dedicated support

**Files to create:**
- `social-automation/frontend/app/(public)/layout.tsx`
- `social-automation/frontend/app/(public)/page.tsx`
- `social-automation/frontend/app/(public)/pricing/page.tsx`
- `social-automation/frontend/app/(public)/features/page.tsx`
- `social-automation/frontend/app/(public)/about/page.tsx`
- `social-automation/frontend/components/marketing/Hero.tsx`
- `social-automation/frontend/components/marketing/FeatureGrid.tsx`
- `social-automation/frontend/components/marketing/PricingTable.tsx`
- `social-automation/frontend/components/marketing/CTASection.tsx`
- `social-automation/frontend/components/marketing/PublicNav.tsx`
- `social-automation/frontend/components/marketing/PublicFooter.tsx`

**Files to modify:**
- `social-automation/frontend/app/page.tsx` (show landing for guests)

**Verification:**
- `tsc --noEmit` passes
- `curl http://localhost:8082/` returns the landing page HTML
- `curl http://localhost:8082/pricing` returns the pricing page
- Unauthenticated visitor sees marketing content, not a redirect

---

### 1.3 Onboarding wizard

**Problem:** New signups land on the dashboard with no guidance. There
is no "connect your first account" flow, no brand setup, no first-post
template.

**Current state:**
- Brand onboarding exists at `app/(dashboard)/brand/onboarding/page.tsx`
  but it's brand-specific, not a general new-user wizard
- No post-registration redirect to a guided setup
- No "connect account" call-to-action on the dashboard for users with
  zero connected accounts

**Tasks:**

1. **Create onboarding wizard** (`app/(dashboard)/onboarding/page.tsx`):
   - Step 1: Welcome + profile setup (name, timezone, company)
   - Step 2: Connect your first social account (OAuth buttons for
     LinkedIn, Facebook, Instagram, Twitter/X, TikTok)
   - Step 3: Brand basics (brand name, voice, colors — or skip)
   - Step 4: Create your first post (template picker or blank)
   - Step 5: Done — redirect to dashboard

2. **Backend onboarding state tracking**:
   - Add `onboarding_completed: bool` field to `User` model
   - `GET /api/v1/auth/me` returns `onboarding_completed`
   - Frontend redirects to `/onboarding` if `!onboarding_completed`

3. **Dashboard empty states**: For users with 0 accounts, 0 posts, 0
   media — show contextual CTAs ("Connect your first account", "Create
   your first post", "Upload your first image").

4. **Onboarding checklist component** on the dashboard sidebar:
   - [ ] Connect a social account
   - [ ] Set up your brand
   - [ ] Create your first post
   - [ ] Schedule a post
   - Progress bar + dismiss when complete

**Files to create:**
- `social-automation/frontend/app/(dashboard)/onboarding/page.tsx`
- `social-automation/frontend/components/onboarding/Wizard.tsx`
- `social-automation/frontend/components/onboarding/StepConnectAccount.tsx`
- `social-automation/frontend/components/onboarding/StepBrandBasics.tsx`
- `social-automation/frontend/components/onboarding/StepFirstPost.tsx`
- `social-automation/frontend/components/dashboard/EmptyState.tsx`
- `social-automation/frontend/components/dashboard/OnboardingChecklist.tsx`

**Files to modify:**
- `social-automation/backend/app/models/user.py` (add `onboarding_completed`)
- `social-automation/backend/app/api/auth.py` (return field in `/me`)
- `social-automation/frontend/app/(dashboard)/dashboard/page.tsx` (checklist + empty states)
- `social-automation/frontend/app/(dashboard)/layout.tsx` (redirect to onboarding)
- Alembic migration for `onboarding_completed` column

**Verification:**
- New user registration redirects to `/onboarding`
- Completing the wizard sets `onboarding_completed = true`
- Returning user skips the wizard
- Dashboard shows empty states when no accounts/posts/media

---

## Phase 2 — Operational Reliability (blocks trust)

### 2.1 Instagram profile updates

**Problem:** All saved sessionids in the instagrapi sidecar are expired.
Username/password login gets 429 (rate limited) or "login with Facebook"
error. Facebook SSO flow has a redirect loop on the OIDC profile picker.
The Instagram account is connected to SocialAuto but the sidecar cannot
edit the profile.

**Current state:**
- Instagram sidecar (port 8011) has full `/account/biography`,
  `/account/external-url`, `/account/picture` PATCH endpoints
- All 7 saved sessions for `cloudless.gr` (PK `36910035385`) are expired
- Instagram Graph API token returns only `{"id":"..."}` — insufficient
  permissions for profile field reads/writes
- Facebook SSO enters redirect loop on profile picker page
- `instagram-profile-manager` skill with scripts already exists

**Tasks:**

1. **Get a fresh sessionid** (manual, one-time):
   - User logs in to Instagram in a real browser
   - Extract `sessionid` cookie (via browser devtools or cookie
     extension)
   - Import via `POST /auth/login/by/sessionid` on the sidecar
   - Persist the new session

2. **Fix Facebook SSO redirect loop**:
   - Debug the profile picker page (`Continue Themistoklis Baltzakis`
     button)
   - The redirect goes to `facebook.com/?crypted_string=...` then back
     to the picker — investigate the `next` parameter encoding
   - Alternative: use the FB sidecar's `/debug/all-cookies` endpoint to
     extract Instagram cookies from an authenticated FB session that
     has already authorized Instagram

3. **Wait for rate limit to clear** (24-48h) then retry username/password
   login with WARP proxy (`socks5://warp-proxy:1080`)

4. **Add session health check**: Create a Celery beat task that checks
   the Instagram sidecar session every 6 hours and alerts (Slack/email)
   if expired.

5. **Document the recovery procedure** in the
   `instagram-profile-manager` skill.

**Files to modify:**
- `.devin/skills/instagram-profile-manager/SKILL.md` (recovery docs)
- `social-automation/backend/app/worker/tasks/instagram_session_check.py` (new)

**Verification:**
- `curl -s http://localhost:8011/account -H "X-Session-ID: ..."` returns
  profile data
- `ig-update-bio.sh "test"` successfully updates the bio
- `ig-update-url.sh "https://cloudless.gr"` successfully updates the URL
- Session health check task runs without errors

---

### 2.2 Facebook personal profile session

**Problem:** The FB browser sidecar shows the logged-out profile picker
page. The saved session (`/data/fb-session.json`) has cookies but
Facebook no longer accepts them — the profile picker appears instead of
the feed.

**Current state:**
- FB sidecar (port 9226) reports `logged_in: true` but the page shows
  "Continue Themistoklis Baltzakis / Use another profile"
- Clicking "Continue" navigates but returns to the same picker page
- The `c_user` cookie exists but may be stale

**Tasks:**

1. **Manual re-login** (one-time):
   - User logs in to Facebook in the sidecar browser (via noVNC or
     direct Playwright)
   - Session is saved to `/data/fb-session.json`
   - Verify the feed loads without the profile picker

2. **Add session validation**: The sidecar's `/session` endpoint should
   check if the page is the profile picker (not the feed) and report
   `logged_in: false` in that case.

3. **Add auto-relogin attempt**: If the session is invalid, try
   navigating to `facebook.com/login` and filling credentials from the
   secret store.

**Files to modify:**
- `facebook-browser-sidecar/server.js` (session validation logic)

**Verification:**
- `curl -s http://localhost:9226/session` reports `logged_in: true` and
  URL is `https://www.facebook.com/` (feed, not picker)
- `fb-personal-update-website.sh "https://cloudless.gr"` works
- Profile page loads without "Log In" button

---

### 2.3 Frontend source structure verification

**Problem:** Initial assessment couldn't find `page.tsx` files. This was
a search error — the files exist. However, the frontend has no public
route group and the root page is a redirect stub.

**Current state (verified):**
- Next.js App Router with route groups: `(auth)` and `(dashboard)`
- 40+ `page.tsx` files across dashboard, auth, brand, content, media,
  settings, analytics, calendar, workflows, MCP stack
- Root `app/page.tsx` is a 20-line redirect to `/login` or `/dashboard`
- No `(public)` route group exists
- Frontend builds successfully, `tsc --noEmit` passes
- Served on port 8082, HTTP 200

**Resolution:** This is not a real issue — the frontend architecture is
sound. The only gap is the missing public/marketing pages (addressed in
1.2). No action needed beyond Phase 1.2.

---

## Phase 3 — Polish & Trust (blocks customer confidence)

### 3.1 API rate limiting / quota enforcement

**Problem:** `slowapi` rate limiter exists but is IP-based (300/min
default), not tier-based. `ai_usage_logs` tracks AI usage but no
middleware enforces per-plan limits. A free user could make unlimited
API calls.

**Current state:**
- `slowapi.Limiter` with Redis storage, IP-based key
- Default: 300/minute globally
- Per-endpoint limits: login 10/min, forgot-password 5/min, AI
  generation 10/min
- No tier-based quotas (free vs pro vs business)
- `ai_usage_logs` table records every AI call with token counts
- No middleware checks "has this team exceeded their plan limit?"

**Tasks:**

1. **Define plan tiers and limits** (in `app/core/config.py`):
   ```python
   PLAN_LIMITS = {
       "free": {"posts_per_month": 10, "ai_calls_per_month": 50, "social_accounts": 1},
       "pro": {"posts_per_month": 100, "ai_calls_per_month": 500, "social_accounts": 5},
       "business": {"posts_per_month": -1, "ai_calls_per_month": 5000, "social_accounts": 20},
       "enterprise": {"posts_per_month": -1, "ai_calls_per_month": -1, "social_accounts": -1},
   }
   ```

2. **Add `plan_tier` field to `Team` model** (default: "free").

3. **Create quota middleware** (`app/api/deps.py`):
   - `check_quota(resource: str)` dependency
   - Queries current month's usage from `ai_usage_logs` / `posts`
   - Raises 429 if over limit with a descriptive message
   - Applied to AI, content creation, and publishing endpoints

4. **Add usage endpoints**:
   - `GET /api/v1/usage` — current month's usage vs limits
   - `GET /api/v1/usage/history` — 12-month usage history

5. **Frontend usage widget**: Show a progress bar in the sidebar
   ("47/50 AI calls used this month") and a usage page in settings.

**Files to create:**
- `social-automation/backend/app/api/usage.py`
- `social-automation/backend/app/core/quotas.py`
- `social-automation/frontend/app/(dashboard)/settings/usage/page.tsx`

**Files to modify:**
- `social-automation/backend/app/models/user.py` (add `plan_tier` to Team)
- `social-automation/backend/app/api/deps.py` (quota dependency)
- `social-automation/backend/app/api/ai.py` (add quota check)
- `social-automation/backend/app/api/content.py` (add quota check)
- `social-automation/backend/app/api/__init__.py` (add usage router)
- Alembic migration for `plan_tier` column
- `social-automation/frontend/components/layout/Sidebar.tsx` (usage widget)

**Verification:**
- Free-tier user gets 429 after 50 AI calls in a month
- Pro-tier user gets 429 after 500 AI calls
- `GET /api/v1/usage` returns correct counts
- Frontend shows usage progress bar
- `pytest tests/unit/test_quotas.py -q` passes

---

### 3.2 Transactional email notifications

**Problem:** An email digest service exists (`email_digest.py`) but it
only sends periodic Slack-style digests. There are no transactional
emails: no "Welcome", "Password reset", "Post published", "Subscription
renewed" emails. Users have no email-based feedback loop.

**Current state:**
- `email_digest.py` supports SMTP (Resend relay) and Cloudflare Email
- `send_email_smtp()` and `send_email_cloudflare_verified()` functions
  exist
- Notification preferences endpoint exists (`/auth/notifications/preferences`)
- No transactional email templates
- No event-triggered email sending (post published, account connected,
  etc.)
- The `notification_preferences` JSON field on `User` exists but is
  only used for in-app notifications

**Tasks:**

1. **Create email template system** (`app/services/email_templates.py`):
   - `welcome_email(user)` — sent on registration
   - `password_reset_email(user, reset_link)` — sent on forgot-password
   - `post_published_email(user, post)` — sent when a scheduled post
     goes live
   - `account_connected_email(user, platform)` — sent when OAuth
     succeeds
   - `quota_warning_email(user, resource, usage, limit)` — sent at 80%
     usage
   - `team_invite_email(inviter, invitee, team, invite_link)` — sent on
     team invite

2. **Wire templates to events**:
   - `register()` → `welcome_email()`
   - `forgot_password()` → `password_reset_email()`
   - Celery publishing task → `post_published_email()`
   - OAuth callback → `account_connected_email()`
   - Quota middleware → `quota_warning_email()`
   - Team invite → `team_invite_email()`

3. **Respect notification preferences**: Check
   `user.notification_preferences` before sending. Add email-specific
   toggles (`email_on_publish`, `email_on_quota`, `email_on_invite`).

4. **Frontend notification settings**: Add an "Email Notifications"
   tab in settings with toggles for each email type.

5. **Email delivery tracking**: Add `email_log` table to record sent
   emails (recipient, subject, status, timestamp) for debugging.

**Files to create:**
- `social-automation/backend/app/services/email_templates.py`
- `social-automation/backend/app/models/email_log.py`
- `social-automation/backend/tests/unit/test_email_templates.py`

**Files to modify:**
- `social-automation/backend/app/api/auth.py` (welcome + password reset)
- `social-automation/backend/app/worker/tasks/publishing.py` (post published)
- `social-automation/backend/app/api/accounts.py` (account connected)
- `social-automation/backend/app/api/teams.py` (team invite)
- `social-automation/frontend/app/(dashboard)/settings/page.tsx` (email toggles)
- Alembic migration for `email_logs` table

**Verification:**
- New user registration triggers a welcome email
- Password reset triggers a reset email with a working link
- Post publish triggers a notification email
- Notification preferences are respected (opt-out works)
- `pytest tests/unit/test_email_templates.py -q` passes

---

### 3.3 Public API documentation

**Problem:** OpenAPI schema exists at `/openapi.json` but Swagger UI
(`/docs`) and ReDoc (`/redoc`) are disabled in production
(`docs_url="/docs" if settings.DEBUG else None`). Currently `DEBUG=true`
in `.env` so they're visible, but this is a security concern — debug
mode should be off in production.

**Current state:**
- `FastAPI(docs_url="/docs" if settings.DEBUG else None, redoc_url="/redoc" if settings.DEBUG else None)`
- `DEBUG=true` in `.env` (should be `false` for production)
- `APP_ENV=production` in `.env`
- 259 endpoints documented in OpenAPI schema

**Tasks:**

1. **Decouple docs from DEBUG**: Always expose `/docs` and `/redoc` but
   require authentication (admin only) or use a separate `EXPOSE_API_DOCS`
   setting.

2. **Create a public API docs page** on the frontend:
   - `app/(public)/api-docs/page.tsx`
   - Embeds Swagger UI or ReDoc via `swagger-ui-react` or
     `@redocly/redux` npm package
   - Fetches the OpenAPI schema from `/api/v1/openapi.json`
   - Shows only public endpoints (auth, register, public content)
   - Hides internal endpoints (admin, ops, cf-db)

3. **Add API key authentication docs**: Document how third-party
   developers can get an API key and use it for programmatic access.

4. **Set `DEBUG=false` in production**: Fix the `.env` to properly
   disable debug mode while keeping docs accessible via the frontend
   page.

**Files to create:**
- `social-automation/frontend/app/(public)/api-docs/page.tsx`

**Files to modify:**
- `social-automation/backend/app/main.py` (decouple docs from DEBUG)
- `social-automation/backend/app/core/config.py` (add `EXPOSE_API_DOCS`)
- `.env` (set `DEBUG=false`)

**Verification:**
- `curl https://social.cloudless.gr/api/v1/openapi.json` returns the
  schema
- `https://social.cloudless.gr/api-docs` renders interactive docs
- `DEBUG=false` and the backend doesn't expose stack traces
- Internal endpoints are not shown in public docs

---

## Implementation order

| Priority | Phase | Task | Effort | Dependencies |
|----------|-------|------|--------|--------------|
| 1 | 1.1 | Multi-tenant team API + isolation tests | Medium | None |
| 2 | 1.2 | Public landing + pricing page | Medium | None |
| 3 | 1.3 | Onboarding wizard | Medium | 1.1 (team model) |
| 4 | 2.1 | Instagram session recovery | Small (manual) | None |
| 5 | 2.2 | Facebook session re-login | Small (manual) | None |
| 6 | 3.1 | Rate limiting / quota enforcement | Medium | 1.1 (plan_tier on Team) |
| 7 | 3.2 | Transactional email notifications | Medium | 1.1 (team invite email) |
| 8 | 3.3 | Public API documentation | Small | 1.2 (public route group) |

## What is explicitly NOT in this plan

- **Stripe billing / payments** — identified as a critical gap but
  requires a separate plan (Stripe integration, webhook handling, plan
  provisioning, dunning, invoices). This plan focuses on the items the
  user explicitly listed.
- **Webhooks (outbound)** — same, separate plan needed.
- **DMCA / ToS / Privacy Policy legal pages** — needed for public
  launch but legal content, not engineering.
- **SSO / SAML** — enterprise feature, deferred.
- **Mobile app** — not in scope.

## Test gate (after each phase)

```bash
# Backend
docker compose exec -T social-api python -m pytest tests/unit -q
docker compose exec -T social-api python -m ruff check app/

# Frontend
cd social-automation/frontend && ./node_modules/.bin/tsc --noEmit --incremental false

# Compose
docker compose config --quiet

# Health
curl http://localhost:8083/health
curl http://localhost:8082/

# Workers
docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping
```
