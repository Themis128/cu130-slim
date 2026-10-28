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

`POST /api/v1/newsletter/linkedin/issue` → `linkedin_newsletter_bridge.py`
(persistent headed profile, ClipboardEvent paste for Trusted Types; base64
payload for non-ASCII). Runbook details live in the file's docstring —
issue #2 was published through it live 2026-10-14.

## Failure modes

- `session dead` → sidecar lost its cookies; re-auth (2FA) then re-export.
- 403 on voyager → `rw_organization_admin` or member-role gap; or LinkedIn
  changed the endpoint shape — check `parse_subscriber` keys.
- Empty list → wrong `seriesUrn`, or the newsletter has zero subs.
