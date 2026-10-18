# intravox-ops — Cloudless intranet on Nextcloud IntraVox 3.1.2

Operate the IntraVox intranet at `https://cloud.cloudless.gr/apps/intravox/` —
pages, widgets, navigation, media uploads, the SocialAuto digest pipeline, and
the verification quirks that bite when scripting against its API.

## Quick use

```bash
export NEXTCLOUD_USERNAME=...   # app-password account
export NEXTCLOUD_APP_PASSWORD=...
python3 .cursor/skills/intravox-ops/scripts/intravox_tool.py <cmd>
```

Commands: `health`, `pages-list --lang en`, `page-get <uid>`,
`page-create <id> <title> --parent en/news/updates`,
`widget-add <uid> --type file-story --widget '{...}'`,
`news --source news`, `nav-get`, `nav-add-child <parent-id> <id> <title> --uid <page-uid>`,
`upload <local> <IntraVox/media/...>` (WebDAV MKCOL+PUT, recursive).

## Verified API contract (burned 2026-10-15)

- Widget `type` strings: `text`, `heading`, `links`, `divider`, `image`,
  `video`, `news`, `feed`, `file-story`, `photo-story`, `calendar`,
  `people`. **Hyphenated** — unknown types are silently stripped on PUT
  (row comes back empty; re-GET to verify, the tool does).
- News widget `sourcePath` is **relative to the language root**: `news`,
  not `en/news` (probe: `GET /api/news?sourcePath=news`).
- `file-story`/`photo-story` config: `folderPath` (user-visible path, e.g.
  `IntraVox/media/Visibility Era`), `mode`, `sortOrder`, `pageSize`,
  `paginationMode`, `tileSize`, `visibleColumns`.
- Page create: `POST /api/pages {id,title,language,parentPath}` → 201 with
  `uniqueId`; content via `PUT /api/pages/{uniqueId}` (full page object:
  `layout.rows[].widgets[]` with `column`, `order`, `content`/`config`).
- Navigation: single doc — `GET /api/navigation`, mutate
  `navigation.items[]` (fields: `id,title,uniqueId,url,target,children`),
  `POST` it back.
- Digest convention: n8n writes `en/news/updates/socialauto-digest-YYYY-MM-DD`
  (heading + text widgets); homepage news widget (`sourcePath: news`)
  surfaces them automatically.
- Monitoring: Prometheus Probes `selfhosted-intravox` +
  `tunnel-cloud` (blackbox, `release: kube-prom`) → `probe_success` in
  Grafana/Alertmanager.
- Apache wedge watchdog on omv: `nextcloud-wedge-watchdog.timer` (2 min) —
  probes the app route inside the container + counts log segfaults,
  restarts on wedge, ntfy `nextcloud-ops`. `status.php` alone does NOT
  detect the wedge (cheap enough to succeed between child crashes).

## SocialAuto live feeds (Phase 2a — live 2026-10-15)

- SocialAuto exposes read-only feeds at `GET {base}/api/v1/service-feed/`
  (`activity`, `digest`, `status`, + discovery `GET /service-feed`).
  Auth: `X-Service-Token: $INTRANET_FEED_TOKEN` (social-api env).
  Response `{items:[{title,url,excerpt,date,author,image}]}` matches the
  IntraVox custom-connection `responseMapping` 1:1.
- Connections stored in appconfig `intravox feed_connections` — tokens are
  **ICrypto-encrypted server-side**, so create them via
  `POST /api/settings/feed-connections {connections:[...]}` (admin), never
  `occ config:app:set`. Fields: `type:'custom'`, `baseUrl`,
  `customEndpoint`, `authMethod:'apikey'`, `apiKeyHeader:'X-Service-Token'`,
  `token` (plaintext on POST — service encrypts), `responseMapping`.
  **401 "Invalid service token" on cron fetches** = stored plaintext
  differed from `INTRANET_FEED_TOKEN` (quotes/whitespace or rotation) —
  GET the connections, re-POST with the exact stripped `.env` value,
  verify via `/api/feed/external`.
- Saved connections: `socialauto-activity`, `socialauto-digest`,
  `socialauto-status` → baseUrl `https://social.cloudless.gr`.
- Feed widget config on a page: `type:'feed'`,
  `sourceType:'connection'` (frontend value — NOT 'custom'),
  `connectionId`, `limit`, `layout:'list'|'card'|'sidebar'`,
  `showDate/showExcerpt/showImage`.
- Verify a connection end-to-end:
  `GET /api/feed/external?sourceType=custom&connectionId=<id>&limit=3`.
- Live widgets: Operations & Infrastructure page (3 feed widgets +
  Dashboards links), Client Work page (`socialauto-leads` connection →
  `/api/v1/service-feed/leads` — names/sources only, never raw emails).
- `links` widget stores its list in **`items`** (`{title, text, url,
  icon, target}`, widget `columns`) at **top level of the widget JSON** —
  `{"type":"links","items":[...],"columns":2}`, NOT inside `config` and
  NOT `links`; a wrong key is silently dropped (widget persists with
  `items: []`), same as unknown widget types.

## Autopilot section (live 2026-10-18)

- Top-level nav `Autopilot` → index page `en/autopilot` (loop contract +
  efficiency rules) with 8 department children under `en/autopilot/`:
  dept-executive, dept-marketing, dept-sales, dept-revenue,
  dept-operations, dept-analytics, dept-comms, dept-security. Each page
  maps that function's real autonomous loops (task names + cadence),
  data surfaces, and human-escalation triggers. Extend the department
  pages in place rather than creating new top-level org pages.

## Guest client shares (Phase 3 enabler)

- `guests` app installed; whitelist includes `intravox` — client-facing
  limited accounts can view shared intranet pages without full accounts.
- Flow: share the page's GroupFolder subtree (or the IntraVox public
  share) → invite the client email via the guests app → client sees only
  that share. Bookings for client calls: `appointments` app (2.8.6,
  NC34-compatible) installed — configure a weekly template + public page
  in Calendar → Appointments; writes into the calendar via CalDAV.
- Grafana has no IntraVox iframe widget — dashboards are linked via the
  `links` widget on Ops page + the `external` top-nav entry.

## Permissions model

GroupFolder `IntraVox` (id 1). Groups: `IntraVox Admins` (full),
`IntraVox Editors` (read/write/delete — content creators),
`IntraVox Users` (**read + share** — Share bit required or public/RSS
feeds go empty). Base permissions cap ACL grants — never grant write only
via ACL over a read-only base.

Approval gate (installed): `approval` app — rule 1: `IntraVox Editors`
request, `IntraVox Admins` approve; hidden tags Pending Review(1) /
Approved(2) / Rejected(3); `unapprove_when_modified=1`.

Guests: `guests` app whitelist includes `intravox` → client-facing
limited accounts can view shared intranet pages.

## File layout

- Pages: GroupFolder `IntraVox` — `en/...` dirs, `page.json` per page.
- Media for widgets: `IntraVox/media/<section>/` (WebDAV-readable, all
  intranet users can render).
- Kakkava library: `IntraVox/media/Visibility Era/` (32 PDFs mirrored from
  OneDrive 2026-10-15 — canonical copy; `/mnt/c` is NOT required anymore).


## Internal links & page schema (verified 2026-10-16)

- Internal page links in `links` widgets use the SPA route
  `/apps/intravox/pages/en/<path>` (e.g. `en/departments/it`), NOT
  `/apps/intravox/en/...` — the latter 404s. Page files are
  `en/<path>/<page-id>.json` in the GroupFolder (`__groupfolders/1/files/`).
- The persisted page object is exactly `{uniqueId, title, language,
  layout}` — there are NO `menu`/`weight`/`tags` fields in IntraVox
  3.1.2. Unknown keys are silently dropped; don't invent metadata fields.
- Ordering/positioning lives in `navigation.json` (`navigation.items[]`
  tree), not in page files.
- In-app docs: "IntraVox Developer Guide" page (`developer-guide`,
  Documentation nav subtree) holds the condensed ops reference.
