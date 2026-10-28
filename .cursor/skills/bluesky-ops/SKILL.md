---
name: bluesky-ops
description: >
  Bluesky (AT Protocol) integration in SocialAuto — the bluesky_api.py client
  (createSession app-password auth, uploadBlob, createRecord with richtext
  facets), the /api/v1/bluesky connect/status endpoints, feed-style publish
  wiring in publishing.py, and the bluesky_tool.py ops CLI. Use for Bluesky
  account connection, publishing, DID/handle/facet issues, or self-hosted
  PDS configuration.
---

# Bluesky ops

Bluesky is SocialAuto's first **AT Protocol** platform — fully free, no app
review, no OAuth dance. Credentials are `handle` + **app password** (created
at Bluesky Settings → App Passwords); the app password is stored encrypted in
`access_token_enc` and a fresh `createSession` runs per publish, so there is
no token-expiry path.

## Connect

```
POST /api/v1/bluesky/connect
{ "handle": "cloudless.gr", "app_password": "xxxx-xxxx-xxxx-xxxx",
  "pds_url": "https://bsky.social" }   # pds_url optional — self-hosted PDS OK
```

Connect does a real `createSession` — bad passwords fail at connect time with
the PDS error surfaced. `account_id` = DID (`did:plc:…`), `username` = handle.

## Ops tool

```bash
docker exec social-api python3 /app/scripts/bluesky_tool.py <cmd>
```

- `accounts` — connected Bluesky accounts (id, handle, DID, PDS)
- `status` — live session + profile (followers/follows/posts)
- `facets "<text>"` — dry-run facet detection; shows byte offsets and
  detected link/mention/tag spans without posting

## Publish path

`platform="bluesky"` in `publishing.py` dispatch → `_publish_bluesky`:

- **Text ≤300 graphemes** (truncated to 295). The auto_assembled caption +
  hashtags often exceed this — keep Bluesky copy tight.
- **Media required** (owner rule): images → `app.bsky.embed.images` (≤4,
  ≤2MB each, jpg/png/webp/gif, aspectRatio hint from PIL); video →
  `app.bsky.embed.video` (ONE mp4 ≤300MB — video replaces images).
- **Facets**: Bluesky does NOT auto-link. `build_facets()` annotates URLs,
  @mentions (resolved to DIDs via getProfile — unresolvable mentions are
  dropped, not posted broken), and #hashtags with UTF-8 **byte** offsets.
- Success → `platform_post_id` = AT URI (`at://did:plc:…/app.bsky.feed.post/<rkey>`),
  `platform_url` = `https://bsky.app/profile/<handle>/post/<rkey>`.

## Constraints / honest notes

- No DMs via this client (chat.bsky.* needs a separate scope setup).
- No OAuth — app password only. If Bluesky ever requires scoped OAuth for
  third-party apps, this client needs the DPoP/PAR flow.
- Rate limits are per-PDS; bsky.social is generous for normal posting.
- Self-hosted PDS is supported via `pds_url` at connect time.

## Status / verification

```
GET /api/v1/bluesky/{account_id}/status
```
→ live createSession + getProfile (followers/follows/posts counts).

## Related

- `x-content-ops` — Bluesky inherits the dev-voice lane (text-first posts
  with links are the AT-native format); the media-required rule still applies.
- `social-content-core` — funnel/quality rules; Bluesky is a brand-funnel
  surface (Polar CTA), not FB-Stars.
