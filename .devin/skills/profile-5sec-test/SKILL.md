---
name: profile-5sec-test
description: Audit every connected social account against the "5-second profile test" (Visibility Era checklist) — name/handle consistency, bio, website link, avatar presence — plus a DMR vision check (ai/qwen3-vl) on whether the avatar is recognizable at 40px icon size. Read-only; fixes go through socialauto-profile / social-accounts-manager. Use when auditing profile completeness across platforms, checking brand consistency, or after onboarding a new account.
---

# Profile 5-Second Test Audit

Scores every connected SocialAuto account against the profile-conversion
checklist (source: Sofia Kakkava, Visibility Era Day 4 — ingested into the
team Chroma collection as `doc:visibility-era-day4:*`):

- **Name present + consistent** — same display name on every brand account
- **Handle typeable + on-brand** — no random digits/underscores
- **Bio present** and mentions/links the site
- **Website link set**
- **Avatar present**, then **vision-scored** by DMR `ai/qwen3-vl`:
  - personal accounts → face fills frame, lit, no sunglasses/filters
  - brand accounts → crisp mark readable as a 40px circle icon

## Run

```bash
# from repo root — needs a bearer token (admin has 2FA; mint one with TOTP)
SA_TOKEN=<token> python3 .devin/skills/profile-5sec-test/scripts/profile_audit.py
SA_TOKEN=<token> python3 ... --json          # machine-readable
SA_EMAIL=.. SA_PASSWORD=.. SA_OTP=.. python3 ...  # creds login instead
```

Env: `SOCIAL_API_URL` (default `http://localhost:8083/api/v1`),
`DMR_URL` (default `http://localhost:12435/engines/v1`),
`DMR_VISION_MODEL` (default `ai/qwen3-vl`).

## Hard platform limits discovered (2026-09-21)

- **Instagram website/name are APP-ONLY.** The web edit page renders the
  Website input `disabled` with "Editing your links is only available on
  mobile." The new edit form has no Name field at all. instagrapi password
  login is currently blocked server-side ("this version of Instagram is out
  of date" — even on aiograpi 2.0.8). FB-SSO sessionids get `login_required`
  on mobile-API calls (web session ≠ API session trust). Bottom line: IG
  name/website edits require the mobile app; don't burn time on web paths.
- **Threads** has no writable website field (PUT ignores it); the bio text
  carries the link. Its `full_name` is inherited from the linked Instagram
  account — changing it requires the IG mobile-app name edit (same limit
  as above); Threads itself exposes no name field.
- **Facebook personal `/profile` scrape exceeds 60s** (up to 3 navigations
  × 20s settle) — the audit passes `timeout=150` for profile reads; a
  "timed out" error on that account is scrape latency, not auth failure.
  The sidecar's own `/session` check (`logged_in`) is the fast probe.
- **TikTok** profile writes are captcha-blocked (tt-ticket-guard); website
  needs a business account anyway.
- **Twitter/X handle** (@screen_name) is not API-writable — account settings
  only. `full_name`, bio, location, website ARE writable via PUT.
- **Facebook profile picker** after session loss can't be synthetic-clicked;
  transplant `c_user`/`xs` cookies from the MCP browser (see
  session-transplant skill) — verified working.
- **LinkedIn sidecar** opens a circuit on 429s (check `/health`
  `rate_limit_until`) — profile reads/writes error until it clears.

## Browser bridge IG-edit selectors (fixed 2026-09-21)

IG's edit form uses `placeholder` attrs, not `name`/`aria-label`:
Website → `input[placeholder="Website"]`, Bio → `textarea#pepBio` /
`textarea[placeholder="Bio"]`, Submit → `div[role="button"]:has-text("Submit")`.
`browser-novnc/browser-bridge.py` `update_instagram_profile` now includes
these alongside the legacy selectors.

## IG session bootstrap recipe (verified 2026-09-21)

1. FB sidecar must be logged in — if it shows the picker, transplant
   `c_user`/`xs` from the MCP playwright browser via `POST :9226/session`
   `{storage_state}`.
2. `login-via-facebook.sh cloudless.gr` drives FB→IG SSO on the sidecar and
   lands a live **web** session (good for reads/browsing; NOT for instagrapi).
3. To reuse that session in the 9223 bridge: `GET :9226/debug/all-cookies?
   domain=instagram.com` → `POST :9223/session/cookies` with playwright-format
   objects. Injection into a running context works; cookies do NOT survive a
   bridge container restart (re-inject after restarts).
