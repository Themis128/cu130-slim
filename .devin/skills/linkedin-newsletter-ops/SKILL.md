# LinkedIn Newsletter Ops — "The Cloudless Build Log"

Publish weekly LinkedIn newsletter editions through SocialAuto. Newsletter lives
on the founder's personal profile (LinkedIn binds newsletters to their author) —
brand *posts* still route to business accounts, but the newsletter itself is the
documented exception (see global_rules).

## Facts

- Newsletter: **The Cloudless Build Log** — ~158 subscribers (Oct 2026)
- Description: "Build-in-public notes on cloud, self-hosted stacks and AI
  marketing. Real numbers, zero fluff."
- Cadence: weekly (n8n `linkedin-newsletter-weekly`, Monday 11:00 Athens)
- First real publish verified 2026-10-14:
  `https://www.linkedin.com/pulse/6100-tests-100-coverage-what-self-hosted-ci-actually-costs-baltzakis-oyrff/`

## Paths

### Path A — automated (intended): `POST /api/v1/newsletter/linkedin/issue`

SocialAuto backend route (`app/api/newsletter.py`, auth header
`X-Service-Token: $NEWSLETTER_SERVICE_TOKEN`). Content sources by precedence:

1. `title` + `html_body`/`text_body` — caller supplies all
2. `article_slug` — reuses the published cloudless.gr weekly article (R2)
3. `topic` — generates a fresh long-form article (≥1,200-word QA target)

Also renders a branded cover (`POST /api/v1/newsletter/cover` standalone),
fills SEO title/description, then calls the LinkedIn sidecar
`POST :9225/newsletter/publish`. `dry_run: true` fills the real editor and
stops before publish. Body limit 110k chars, title 150.

⚠️ **Sidecar editor hydration is flaky** (as of 2026-10-14) — see
"Editor boot behavior" below. Path A timed out on `httpx.ReadTimeout` at 420s
when the sidecar's editor never mounted. Path B was used for the live publish.

### Path B — browser-novnc bridge fallback (VERIFIED WORKING)

The shared bridge (`browser-novnc` :9223) has a **different Chromium profile**
that LinkedIn trusts — `/article/new/` hydrated in ~15s where the sidecar
looped for 240s+. If the sidecar editor won't boot, drive the bridge:

```
POST /session/start  {"platform":"linkedin","interactive":true,"force":true}
                     (persistent profile already holds a live session;
                      force preempts whichever platform holds the browser)
POST /session/navigate {"url":"https://www.linkedin.com/article/new/"}
POST /session/fill   {"selector":".article-editor-headline__textarea","value":TITLE}
```

**Body insert — synthetic paste into ProseMirror** (Trusted Types blocks
`execCommand('insertHTML')`; direct `innerHTML` desyncs the model):

```js
const pm = document.querySelector('div.ProseMirror[contenteditable=true]');
pm.focus();
const dt = new DataTransfer();
dt.setData('text/html', HTML);           // base64-decode inside eval if needed
pm.dispatchEvent(new ClipboardEvent('paste',
  {clipboardData: dt, bubbles: true, cancelable: true}));
```

Verified: ProseMirror consumes the DataTransfer and renders with its own
classes. Two pitfalls burned Issue #2's first version:

- **Encoding:** `atob(b64)` returns raw bytes — feeding them straight to
  `setData` reads them as Latin-1 and produces mojibake (`—` → `â€"`,
  `€` → `â‚¬`, `’` → `â€™`). Decode as UTF-8:
  `new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c=>c.charCodeAt(0)))`.
  Always sanity-check `innerText` for `â`/`ï¿½` before publishing.
- **Replace vs append:** `execCommand('selectAll')` does NOT expand into the
  PM selection — pasting then appends. Clear via DOM range
  (`Range.selectNodeContents(pm)` + `execCommand('delete')`), verify
  `pm.innerText.length <= 1`, *then* paste. Verify the paste left exactly
  one copy (`(innerText.match(/<phrase>/g)||[]).length === 1`).

To fix a published issue: navigate `/article/edit/<article-id>/`, replace the
body the same way, click **Update** — republishes in place, no new feed post.

**Publish-dialog post text uses Quill, not ProseMirror** — synthetic paste is
ignored there; use `document.execCommand('insertText', false, text)` on
`div[contenteditable=true].ql-editor` inside `[role=dialog]`.

**Cover upload**: `POST /session/upload` with
`{"selector":"input[type=file]","file_path":"/tmp/cover.png",
 "click_selector":"button:has-text('Upload from computer')"}`
(docker cp the PNG into browser-novnc first).

**SEO title/description**: `Manage` dropdown → `Settings` → dialog has
`#emberNNN` input (SEO title) + textarea (SEO description, 160 max) → `Save`.
Use the native value setter + `input` event (React controlled).

**Publish**: `Next` → dialog "Tell your network what this edition of your
newsletter is about…" (fill via insertText above) → `Publish`. Success =
redirect to `/pulse/<slug>/` + "Congrats on publishing" dialog.

⚠️ The publish-dialog `Dismiss` (X) opens a "Discard draft?" confirm —
clicking `Discard` returns to the editor and the article draft survives
(auto-saved at `/article/edit/<id>/`), so it's safe for SEO detours.

**Busy-hold**: every bridge call needs `X-Platform: linkedin`; the first
interaction holds the browser 180s (interactive sessions: 15min), foreign
platforms get 409 while held.

## Editor boot behavior (sidecar, 9225)

Observed 2026-10-14 across ~10 attempts:

- Headless Chromium: `/article/new/` shows the LinkedIn splash forever —
  anti-bot skeleton; feed redirects/CSRF walls. **Headed under Xvfb fixes
  hydration** (started with `xvfb-run`; mem_limit raised 2.5G→6G after
  cgroup `oom_kill` at `memory.peak == memory.max`).
- Even headed, cold boots are slow (>240s) — second attempt on a warm
  browser (bundles/service-worker cached) mounts `div.ProseMirror` in ~45s.
  Handler has a retry-nav for this.
- Remove `--enable-unsafe-swiftshader` / software-WebGL flags — LinkedIn's
  GPU probing made the renderer allocate >4G.
- If the sidecar page bounces `/article/new/` ↔ `/feed/` in a loop, that
  session/profile is likely flagged — use the bridge (Path B).

## Session & failure signatures

| Signature | Meaning | Fix |
|---|---|---|
| `403 CSRF check failed` on all voyager calls | `li_at` invalidated | Credential login (`POST /login`) or noVNC; never transplant `li_at` (fingerprint-bound — global revoke) |
| `{"two_factor_required":true}` | SMS/push 2FA | `POST /login` with `verification_code`, or app push approval; each retry sends a NEW code |
| `article-editor-dead-*` in `/data/tmp` | Editor never mounted | Warm-nav retry; if persistent → Path B |
| Page `context destroyed` repeatedly | SPA reload loop / crash | Check `memory.peak` vs limit for OOM; restart container (session persists in `/data/li-session.json`) |
| `channel_not_found` on Slack notify | `SLACK_NEWSLETTER_CHANNEL_ID` (`C0BBDKY6Q9E`) is stale — no `#newsletter` channel exists in the bot's workspace | Post to `#socialauto-publishing` (`C0C1B8PMTUN`) or fix the env var — **known bug**: the n8n weekly workflow's Slack node will fail the same way |

## n8n weekly automation

- Workflow `linkedin-newsletter-weekly`: Monday 11:00 Athens schedule +
  manual webhook. Resolves the weekly article → backend route → Slack notify.
- Slack channel ID is stale (above) — fix before relying on notifications.

## Manual verify checklist after publish

1. URL redirected to `/pulse/<slug>-<hash>/`
2. Page shows "The Cloudless Build Log" + subscriber count
3. Feed post with announcement text exists on the author profile
4. Slack channel got the notification
