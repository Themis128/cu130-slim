# LinkedIn Newsletter Ops — subscribers → CRM + publish pipeline

Owns the weekly subscriber sync and the newsletter publish path.

## Subscriber sync (`newsletter-subs-sync`, Sundays 09:15)

**No public API exists.** The sync calls the same authenticated Voyager
endpoint LinkedIn's own dashboard uses:

    GET /voyager/api/voyagerPublishingDashSeriesSubscribers
        ?q=contentSeries&seriesUrn=urn:li:fsd_contentSeries:<id>&count=50&start=N

via the **linkedin-sidecar's exported cookies** (`li_at` + `JSESSIONID` as
csrf-token). Each subscriber → name, headline, profile URL, URN.

- **Dedupe**: `LI-URN:<urn>` marker in the Espo Lead `description`
  (contains-search) + Redis set `linkedin-newsletter:synced-urns` — a
  re-run never double-creates.
- **No email**: subscribers become awareness-stage Leads with
  `source: "LinkedIn newsletter"`, `title` = headline. No outreach — they
  haven't given a contact channel.
- **Activation**: set `LINKEDIN_NEWSLETTER_SERIES_URN` in `.env`. Until
  then the beat no-ops cleanly.

## Scripts

- `scripts/find-series-urn.sh` — list your newsletter series + URNs so you
  can set the env var (opens the newsletter on the sidecar session).
- `scripts/test-sync.sh` — dry-run the sync against the live stack:
  exports sidecar cookies, fetches page 1, prints subscriber count +
  first names, makes NO CRM writes.
- `scripts/check-session.sh` — is the sidecar's LinkedIn session alive?
  (2FA-gated — if pending, approve the push first per human-gate-ops.)

## Publish path (context)

`POST /api/v1/newsletter/linkedin/issue` → `LinkedInSidecarClient.
publish_newsletter_issue` → sidecar `POST :9225/newsletter/publish`
(warm `/feed/` → `/article/new/` → ProseMirror wait ≤240s + one retry →
newsletter scope → base64 cover upload → title/body/SEO → publish or
schedule). `browser="sidecar"` forces it; default falls back to
`LinkedInNewsletterBridge` (browser-novnc :9223, persistent profile —
verified for Issue #2 on 2026-10-14, but its LinkedIn session can be
logged out independently of the sidecar's).

### Direct-sidecar bypass (engine-bounce survival)

`scripts/newsletter_publish_direct.sh` assembles the payload inside
social-api with app modules (`blog_articles.get_published_article` +
`newsletter_cover.render_newsletter_cover`), writes it to
`/app/uploads/newsletter_payload.json` (bind-mounted → host), then fires
a **detached host curl** straight at `http://<sidecar-ip>:9225/
newsletter/publish` with `--max-time 1500`. Survives social-api
restarts; only a full engine bounce kills it. Use when the API path
keeps 502ing to `ReadTimeout`/`RemoteProtocolError` while the sidecar
itself is healthy.

`scripts/newsletter_publish_autopilot.sh` wraps that in an
unattended retry loop that runs on the **WSL host** (survives engine
bounces that kill in-container callers): engine-down →
`wsl-docker-doctor fix` → wait for api+sidecar healthy → warm feed →
direct publish → poll result → definitive success exits 0, failures
cooldown+retry until `--deadline-min` (default 180). flock-guarded;
status in `~/logs/newsletter_autopilot.status.json`. Launch:

    setsid nohup .devin/skills/linkedin-newsletter-ops/scripts/newsletter_publish_autopilot.sh \
      > ~/logs/newsletter_autopilot.nohup 2>&1 &

## Failure modes

- `session dead` → sidecar lost its cookies; re-auth (2FA) then re-export.
- 403 on voyager → `rw_organization_admin` or member-role gap; or LinkedIn
  changed the endpoint shape — check `parse_subscriber` keys.
- Empty list → wrong `seriesUrn`, or the newsletter has zero subs.

### 2026-11-11 battle-test additions

- **`article_slug` resolves but body is empty** → R2 article JSON stores
  `html`/`content`, NOT `contentMarkdown`; `newsletter.py` only read
  `contentMarkdown` → sidecar got `html_body or text_body is required`.
  Fixed: prefer `article["html"]`, fall back to markdown→html of
  `contentMarkdown|content`. Verify slug resolution with
  `blog_articles.get_published_article(slug)` before blaming the sidecar.
- **Sidecar `ReadTimeout` at ~420s** → cold editor mount + fill + publish
  legitimately takes 10–18 min; `LinkedInSidecarClient` timeout raised
  420→900s. The sidecar keeps working after the client disconnects — a
  502 does NOT mean nothing published; always verify on LinkedIn before
  retrying (duplicate issue risk).
- **`article editor did not mount` / splash-loop** → warm the sidecar
  first (`POST :9225/debug/navigate {"url":".../feed/"}`, poll `/health`
  until `page_ok:true`), THEN fire publish. Cold-start inside the
  publish budget wastes the 240s editor wait on browser boot.
- **Docker/WSL engine bounces every ~10–20 min** (docker-recovery cron +
  vsock degradation) kill in-flight API requests → use the direct-sidecar
  script; run `wsl-docker-doctor fix` between attempts.
- **pr-land-watch merge treadmill restarts api+workers+beat** on every
  landed PR and `git pull`s the tree — uncommitted `app/` edits can be
  wiped mid-session (it now declines when modifications are <30min old,
  but older dirty files still get reset). Land backend fixes via PR
  promptly; check `git status` + `grep` your edits before retrying.
- **Verification surfaces** (sidecar `/debug/eval` needs `expr` wrapped
  so it `return`s a value; `/debug/eval` 500s while the page navigates —
  retry):
  - Newsletter manager: `/mynetwork/network-manager/newsletters/` lists
    "The Cloudless Build Log · Author — N subscribers" + series URL
    `/newsletters/the-cloudless-build-log-<id>/`.
  - Drafts count on `/in/<slug>/recent-activity/all/` — unchanged count
    after failed attempts means nothing was saved as draft.
  - `/article/manage/drafts/` = the draft manager.
  - Do NOT navigate the sidecar page while a publish is in-flight —
    `page.goto` aborts the handler's evaluate calls and can kill the run.
