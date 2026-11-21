---
name: instagram-lane-ops
description: >
  Diagnose and repair the two Instagram credential lanes in SocialAuto —
  business_login (graph.instagram.com, canonical publish lane) vs
  fb_onboarding (Page-linked token, needs Page Publishing Authorization).
  Covers the Graph #10 "Requires instagram_content_publish" error (which
  means PPA/linkage incomplete, NOT a missing scope), the lane-doctor
  probe tool, target re-pointing, token refresh without password, the
  passwordless email-login-link recovery lane, SMS 2FA via ADB, and the
  duplicate-publish hazard when retrying queue rows.
---

# Instagram lane ops — business_login vs fb_onboarding

One IG profile (@cloudless.gr, ds_user_id `36910035385`) can be reachable
through **two different Meta credential products**, each stored as its own
`social_accounts` row. They are NOT interchangeable — picking the wrong
one is the entire #10 incident class.

## The two lanes

| | `login_type=business_login` | `login_type=fb_onboarding` |
|---|---|---|
| Token | Instagram User access token | Facebook Page token |
| Host | `graph.instagram.com` | `graph.facebook.com` |
| Scopes | `instagram_business_*` | `instagram_*` + `pages_*` |
| Publish dep | none — works standalone | Page↔IG link + **PPA confirmed** |
| Refresh | `GET graph.instagram.com/refresh_access_token` (beat: weekly, **no password**) | FB token lifecycle |
| App | "Cloudless - IG" (`INSTAGRAM2_CLIENT_ID` `1462851805249610`) | FB app `1936126137016578` |
| OAuth | `/api/v1/auth/oauth/instagram2/authorize` → `/callback` | `instagram-onboarding` → facebook callback |

`publishing.py` chooses the client per row via
`meta_data["login_type"] == "business_login"` → `use_business_login_api=True`.
Post-creation auto-select (`app/api/content.py`) **prefers the
`business_login` row** — it is the canonical publish lane.

Current rows (2026-11-22):

- `38ddbd44` business_login — **canonical, proven live** (published
  `instagram.com/p/DgBaCPZilma/`). IGSID `28747382798219804`.
- `d2215106` fb_onboarding — valid token, Page links the right IG, but
  `#10` persists → PPA incomplete. Keep `active` (reads work); never
  publish through it.
- `9eded1e5` fb_onboarding — expired, points at a different IG object;
  cosmetic drift (`UNDERSTATED` in account-truth).

## Error #10 is a linkage state, not a missing permission

```
POST graph.facebook.com/v26.0/{ig-user-id}/media
→ (#10) Requires instagram_content_publish permission to manage the object
```

`debug_token` will show `instagram_content_publish` already granted — the
message lies. Per Meta's Content Publishing doc: *"an Instagram
professional account connected to a Page that requires Page Publishing
Authorization (PPA) cannot be published to until PPA has been completed."*

Diagnostic matrix (all verified live 2026-11-22):

| Probe | Means |
|---|---|
| `debug_token` scope granted + `/{page}?fields=connected_instagram_account` returns the right IGSID + `/{ig}/media` → #10 | **PPA incomplete** — link exists but publish not authorized. "linked-ok ≠ authorized". |
| `connected_instagram_account` empty | Page↔IG link missing entirely |
| `connected_instagram_account` = different IGSID | Token's Page links another IG — wrong object |
| `graph.instagram.com/me` 200 | business_login lane usable |

## Tools

```bash
# Lane doctor — the canonical probe (run in social-api)
docker exec social-api python3 /app/scripts/ig_lane_doctor.py report
docker exec social-api python3 /app/scripts/ig_lane_doctor.py pending
docker exec social-api python3 /app/scripts/ig_lane_doctor.py repoint <acct-id-prefix>
docker exec social-api python3 /app/scripts/ig_lane_doctor.py publish-probe <acct-id-prefix>
```

- `report` — per-row token validity on the correct host + Page↔IG linkage
  + publish-lane verdict.
- `pending` — pending IG post_targets grouped by lane.
- `repoint` — move ALL pending IG targets to an account (the fix used for
  the 5 stranded fb_onboarding targets).
- `publish-probe` — creates a media container and **never publishes it**
  (containers expire server-side). Definitive publish-lane check without
  shipping a post. This is how fb_onboarding's lingering #10 was proven.

Sibling tools: `instagram_tool.py` (host-aware — business_login rows hit
graph.instagram.com), `instagram_reauth.py` (FB-login lane reauth),
`account_truth.py` (db-vs-live drift).

## Fixing a publish-broken lane — decision order

1. **business_login row exists + token refreshable** → done. The weekly
   `refresh_instagram_tokens` beat refreshes `graph.instagram.com` tokens
   with NO password/session needed — this is how `38ddbd44` healed itself
   once the Meta lock lifted. Check `token_expires_at`; a live beat refresh
   flips status back to `active` automatically.
2. **Pending targets stuck on fb_onboarding** → `ig_lane_doctor repoint`
   to the business_login row (or SQL equivalent). No queue-row edits —
   `publish_queue` binds (post_id, social_account_id) at claim time.
3. **fb_onboarding must publish** → complete PPA (below). Until then any
   new reauth via `instagram_reauth.py` produces another #10-broken row —
   the fast path only mints the token, it does not complete the link.

## PPA completion (fb_onboarding publish unblock)

Two paths, both end at "Confirm Your Facebook Page and Instagram
connection":

- **FB web**: facebook.com → switch to the Page → Settings → Linked
  accounts → Instagram row → Review → "Confirm connection". May ask for
  the IG password (ours is **stale** — see credentials).
- **IG phone app**: profile → Edit profile → Page → "Review your Page and
  Instagram connection" → Confirm. Errored with "Something went wrong"
  during the 2026-11-21 lock window; retry is cheap via ADB.

Verify after: `ig_lane_doctor publish-probe <fb row>` — container create
must succeed, not merely `linked-ok` on the Page edge.

## Recovery ladder for IG login itself

Ranked by prerequisites (use lowest that works):

1. **Passwordless email link** — Meta's "we've made it easy to get back
   on Instagram" mail to `baltzakis.themis@gmail.com` contains
   `instagram.com/_n/web_emaillogin?uid=…&token=…`. Fetch it via
   gmail-local MCP or raw Gmail API
   (`/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/gmail-token.json`,
   scope gmail.readonly; read `format=full`, decode `text/html` part —
   gmail-local's reader returns `body:""` for HTML-only mails). Open in
   the sidecar → IG session without password.
2. **Saved-profile picker + SMS 2FA** — instagram.com picker →
   cloudless.gr → "Try another way" → "See more options" → **Text
   message** → code arrives on the Galaxy S22+ SMS inbox within ~10s;
   read it via `adb shell content query --uri content://sms/inbox`
   (`instagram_reauth.py` does this automatically). Instagram reuses the
   same 6-digit code across resends for a while.
3. **TOTP** — `META_TOTP_SECRET_IG` in `.env` (enrolled on @cloudless.gr,
   verified working 2026-11-20) — **rejected 2026-11-22, likely rotated**
   during the lock churn. Re-enroll via accountscenter → password_and_security
   → two_factor, then update `.env`.
4. **WhatsApp 2FA — DEAD LANE** — the phone's WhatsApp is stuck at
   "Verifying your number"; no WA codes ever arrive. Always switch to
   Text message in the method picker.
5. **Stale `INSTAGRAM_PASSWORD`** — credential-form login rejected it
   live 2026-11-22. Update `.env` when the real password is known (reset
   link: `instagram.com/accounts/password/reset/confirm` in the same
   Meta email thread). Needed by instagrapi private-API + PPA confirm.

## Sidecar driving gotchas (learned live)

- `/debug/activate` takes **`{"index": N}`** — NOT `urlPart` (silently
  defaults to last page → evals land on the wrong tab).
- `/debug/pages` lists all context pages with `active` flag — re-list
  after every popup; **stale OAuth/OIDC popups hijack tabs** and navigate
  them to facebook.com mid-flow.
- Synthetic `el.click()` selects radios but does NOT submit Meta React
  forms — use `/debug/click` real-mouse events (coordinate clicks) for
  submits. The reauth script encodes this (`_CLICK_POS_JS`).
- `/debug/navigate` on the ACTIVE page only; a flapping sidecar
  (429s/browser restarts) drops all pages — check `/debug/pages` first.
- Meta throttles login attempts (~1/hr soft). `instagram_reauth.py`
  self-throttles via `/tmp/instagram-reauth-state.json` — don't fight it.

## Duplicate-publish hazard

A post can hold targets on BOTH IG rows. When one lane published and the
twin's queue row failed, **do not requeue the failed row** — success
would ship the same post to the same profile twice. Check
`post_targets.platform_url` for a live URL first. (Post `4f822b64`,
2026-11-22: published via business_login while its fb_onboarding twin
failed — left failed on purpose.)

## Config state

`.env`: `INSTAGRAM2_CLIENT_ID=1462851805249610`, `INSTAGRAM2_CLIENT_SECRET`,
`INSTAGRAM2_REDIRECT_URI` (default
`http://localhost:8083/api/v1/auth/oauth/instagram2/callback` — must be
whitelisted in the "Cloudless - IG" app → Instagram product → "API setup
with Instagram login"). The dev-dashboard "Generate access tokens" table
can mint a token for @cloudless.gr directly once an IG web session
exists — but our own `instagram2` authorize URL reaches the same OAuth
consent and stores the row end-to-end.
