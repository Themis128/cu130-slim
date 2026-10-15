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
