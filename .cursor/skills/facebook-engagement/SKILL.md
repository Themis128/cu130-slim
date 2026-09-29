# Facebook engagement ops

Comment on public posts + complete the personal profile's weekly
professional-dashboard tasks via automation, when the Graph API can't help.

Use when: the professional dashboard weekly challenge asks for comments/posts/
reels/stories on the personal profile, when the user asks to "comment on X
public posts", or when Page-level API paths can't reach profile-level tasks.

## What the Graph API can and cannot do

- `POST /{object-id}/comments` needs a **Page access token** +
  `pages_manage_engagement`; the comment author is **the Page**.
- User-token comment publishing was removed (v2.10, `#3 OAuthException`).
  **There is no API path to comment as the personal profile.**
- Comments by the Page do not tick the personal profile's weekly counter.

So profile-level engagement = browser automation only, via the Playwright MCP
browser (see `playwright-mcp-driver` skill) with a session transplanted from
the FB sidecar (see `session-transplant`).

## Scripts

- `scripts/transplant_fb.py` — pulls `c_user`/`xs`/`datr`/`fr`/`sb`/`wd`/`dpr`/
  `presence` from the sidecar's `GET /debug/all-cookies`, injects them into the
  MCP profile via `document.cookie` (httpOnly writes work), verifies login.
  Run this first if the MCP profile lacks a facebook.com session
  (`profile/Default/Cookies` sqlite → no `.facebook.com` rows = transplant).
- `scripts/fb_comment.py --url <page-url> --text "comment" [--post-index N]`
  — navigates to a public Page, clicks the "Comment on X's post" button if the
  composer is lazy-rendered, types at human speed, submits with Enter, verifies
  the comment text appears in the DOM. Exit 0 on verified post.

## Weekly professional-dashboard tasks

Read the task list live via the FB sidecar (port 9226):

```bash
curl -X POST localhost:9226/debug/navigate -d '{"url":"https://www.facebook.com/professional_dashboard/status/"}' -H 'Content-Type: application/json'
curl -X POST localhost:9226/debug/eval -d '{"script":"document.body.innerText"}' -H 'Content-Type: application/json'
```

Automation map per task type:

| Task | Path |
|---|---|
| public posts | `POST /api/v1/content/posts` → `target_account_ids=[facebook/user acct]` — publishes via `_publish_facebook_via_sidecar` |
| posts with photos | same + `media_ids` (image counts toward BOTH counters) |
| reels | same + vertical mp4 (ffmpeg Ken Burns, 1080x1920, ≤15s, h264) — posts via `/post/video`; FB usually counts short verticals as reels, verify counter |
| stories | sidecar/bridge story composer — usually already done |
| comment on public posts | `fb_comment.py` on relevant public Pages |
| new followers | NOT automatable — organic result of the above |

## Personal-profile publishing (sidecar)

`facebook/user` accounts publish **only** via the sidecar — Meta removed
profile posting from Graph, so `_publish_facebook` never falls through to
it (a Graph attempt can only produce the misleading "#200 group" error).
Posts default to **public** (Friends-only can't earn followers); override
per-post via `platform_specific.facebook_privacy`.

Sidecar publish internals worth knowing when debugging:

- Composer trigger mounts lazily — `waitForComposerTrigger()` waits up to
  15s; a bare `count()` after `settle()` races and 404s.
- `dismissOpenDialogs()` (Escape + corner click) runs before opening the
  composer — leftover notifications/composer panels intercept clicks.
- Multi-step composer: "Next" advances through review panes (the video
  flow adds a trim/review step) until the final "Post". `clickPost()`
  keeps clicking Next while it is offered — matched by exact innerText
  + on-screen rect (`x >= 0`), because Facebook renders off-canvas
  decoy buttons (negative x, empty text, aria-label only) that
  aria/has-text locators click instead. Disabled Next/Post are skipped
  so video-processing time gets waited out.
- Messages ending in `#tag` open an autocomplete listbox that covers the
  Post button — `clickPost()` dismisses it with a real Escape.
- `setPrivacy()` works inside the composer dialog: opens the audience
  chip, clicks the `<label>` wrapping the `[role=radio]` option
  ("Public"), then confirms the sheet's own Done/Save. On `/post/video`
  it runs AFTER the file upload — the video dialog mounts its own
  audience control and discards earlier choices.
- Success is verified, not assumed: `verifyPosted()` reloads
  `facebook.com/me`, requires the post to contain the message's first
  line AND carry a fresh timestamp (<15 min / "Just now"), and returns
  the real permalink. `posted:true` without `url`/`post_id` is a failure.
- The new profile feed has no article/FeedUnit markers and obfuscates
  timestamps (scrambled aria-hidden spans) — when the feed scan finds
  nothing, `verifyViaContentLibrary()` falls back to
  `professional_dashboard/content/content_library/` whose rows read
  "Published • Today at H:MM AM" and carry `content_id=<b64>` with the
  numeric post id inside. The row's publish time must be >= the click
  timestamp (same-day identical captions are rejected), then
  `/<slug>/posts/<id>` (or `story.php` for `profile.php` profiles)
  redirects to the canonical permalink (`/reel/<id>` for reels).
  `/post/video` allows 300s client-side for processing + verification.

## Profile analytics (pro-mode profiles)

The Graph API exposes no follower/insights edge for personal profiles
(`followers_count` and `/me/feed` fail). `FacebookSidecarClient.
get_profile_stats(expected_name=…)` scrapes the real numbers:
followers from `facebook.com/me`, and 28-day Views / Engagement /
Net follows from `professional_dashboard` (results cached 5 min; the
`expected_name` guard refuses to attribute stats when the shared sidecar
session is a different profile). `sync_facebook_account` writes a
`FollowerSnapshot` + `profile_dashboard` event per sync for
`account_type=user` accounts — they surface via `GET
/api/v1/analytics/accounts/{id}/insights`.

## Commenting rules

- **Genuine, per-post comments only** — read the post text first, reference its
  actual content. Generic "Great post!" is spam-flag bait.
- **Pace**: ≥45-60s between comments, max ~5/day on a personal profile.
- **Targets**: public Pages relevant to the account's sphere (for cloudless:
  Cloudflare, n8n, Raspberry Pi, GitHub, Home Assistant). Group posts and
  friends-only posts do not count as "public posts".
- The comment composer: `div[contenteditable][aria-label*="Comment as"]`; if
  absent, click `div[role="button"][aria-label*="Comment on"]` first (lazy render).
- Verify post-submit via `document.body.innerText.includes(snippet)` inside
  `page.evaluate` — `document` is NOT defined in `browser_run_code_unsafe`.
