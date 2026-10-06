# nextcloud-integration

Integrate the omv Nextcloud workspace (`cloud.cloudless.gr`, NC 34) with
SocialAuto, n8n workflows, and cloudless.gr. Covers WebDAV ops, OCS public
shares, the n8n nextCloud node pitfalls, drop-folder automation, and the
canonical credential layout.

For stack ops (containers, Talk, tunnels, storage) see `omv-nextcloud-ops`.

## Credentials — app password only

- Authenticate with a **Nextcloud app password**, never the account login
  password. Create: `docker exec -u www-data nextcloud php occ user:add-app-password '<uid>'`
  (on omv via SSH). It prints the token once — store it, don't echo it.
- Env vars consumed by `scripts/nextcloud-dav.py` and the SocialAuto backend:
  ```
  NEXTCLOUD_EXPORT_ENABLED=true
  NEXTCLOUD_DAV_URL=https://cloud.cloudless.gr/remote.php/dav/files/<uid>
  NEXTCLOUD_USERNAME=<uid>
  NEXTCLOUD_APP_PASSWORD=<app-password>
  NEXTCLOUD_EXPORT_ROOT=SocialAuto
  ```
  Live values live in the repo-root `.env` (gitignored); `.env.example`
  documents the shape. `nextcloud-dav.py` falls back to reading `.env`.

## Folder layout (workspace convention)

```
SocialAuto/            media mirror root (NEXTCLOUD_EXPORT_ROOT)
  media/               auto-exported assets, mirrors storage_path Y/m/d folders
                       (media_storage.py strftime("%Y/%m/%d") — e.g. media/2026/10/05/…)
  _inbox/              DROP FOLDER → n8n drafts a post per file
    processed/         handled files land here (timestamped)
    failed/            error-branch quarantine — files that failed any pipeline
                       step (download/upload/copy/draft) MOVE here so the
                       15-min schedule doesn't retry them forever. The scan
                       skips `/processed/` and `/failed/` paths.
Marketing/checklist/   monthly automation-checklist.pdf versions
Client-Portals/        per-client share folders (see client-portal flow)
```

## `scripts/nextcloud-dav.py` — the DAV tool

```bash
python3 .devin/skills/nextcloud-integration/scripts/nextcloud-dav.py \
  list SocialAuto/media | upload ./a.png SocialAuto/media/a.png | \
  move a b | mkdir dir | delete path | download r l | share path [--label L] [--expire YYYY-MM-DD] [--password P]
```

- `share` prints the public URL (`https://cloud.cloudless.gr/index.php/s/<token>`).
  Always sends explicit `permissions=1` (read-only): Nextcloud has a bug where
  omitted permissions apply a wrong mask (GH nextcloud/server#37774).

## WebDAV notes that bit us (verified 2026-10)

- **Two DAV roots exist**: `/remote.php/dav/` (current) and
  `/remote.php/webdav/` (legacy alias — both live, 207 on PROPFIND).
  Per-user files root for API calls: `/remote.php/dav/files/<uid>/`.
- PROPFIND `d:href` values are absolute paths — strip the
  `/remote.php/dav/files/<uid>` prefix yourself; collections end with `/`.
- `MOVE` needs a **full URL** `Destination` header.
- `MKCOL` is idempotent: 201 created, 405 already exists.
- OCS Share API: `POST <base>/ocs/v2.php/apps/files_sharing/api/v1/shares`
  with `OCS-APIRequest: true`, `shareType=3` = public link, `shareType=10` =
  Talk conversation share.

## n8n integration — DO NOT use the nextCloud node for path-sensitive ops

- **n8n issue #8802 (open)**: the `nextCloud` node's folder-list does
  `d:href.slice(19)`, hardcoded for `/remote.php/webdav/` prefixes. With the
  modern `/remote.php/dav/files/<uid>` credential URL it returns mangled
  `es/<uid>/...` paths → downstream download/move fails "could not be found".
- **Pattern that works** (see `n8n-workflows/nextcloud-drop-to-draft.json`):
  plain `httpRequest` nodes — `PROPFIND` + `Depth: 1` to list, `GET` for
  download (`options.response.responseFormat: "file"` → binary `data`),
  `MOVE` + full-URL `Destination` header. Auth = **`httpBasicAuth` credential**
  (`type: "httpBasicAuth"`, fields `user`/`password` — NOT the `nextCloudApi`
  type which wants `webDavUrl`).
- **Workflow JSON required fields for import** (learned the hard way):
  top-level `id` (string slug — import fails "null id" without it), `name`,
  `nodes`, `connections`, `settings`; and **`webhookId` on every webhook
  node** — the UI assigns it, imports must carry it explicitly or the
  production webhook never registers (n8n 2.x: `active:true` +
  `activeVersionId` alone is not enough).
- Deploy: `n8n_deploy_workflow` MCP (imports + publishes) →
  `n8n_activate_workflow` → **restart n8n container** — triggers/webhooks
  register only at activation/startup.
- Login nodes need the `Generate TOTP` code node (`SOCIAL_TOTP_SECRET`) feeding
  `otp` on the form body — copy verbatim from `twitter-text-post.json`.

## SocialAuto backend integration (shipped)

- `app/services/nextcloud_export.py` — mirrors every `media_assets` row to
  `<root>/media/<storage_path>` via WebDAV MKCOL+PUT; records
  `meta_data.nextcloud_path`; `create_public_share()` wraps the OCS call.
- `app/worker/tasks/nextcloud.py::export_media_to_nextcloud` — media queue,
  enqueued from `media_storage.py` alongside auto-tag when
  `NEXTCLOUD_EXPORT_ENABLED=true`. Never raises into the request path.
- Manual export of an existing asset:
  `docker exec social-worker-media celery -A app.worker.celery_app call app.worker.tasks.nextcloud.export_media_to_nextcloud --args='["<asset_uuid>"]'`

## Drop-folder → draft workflow (n8n `nextcloud-drop-to-draft`)

- Every 15 min (schedule) or `POST /webhook/nextcloud-drop-to-draft` (manual):
  PROPFIND `SocialAuto/_inbox` → per file: download → `/api/v1/media/upload`
  → `/api/v1/ai/generate-content` → `POST /api/v1/content/posts` (**draft,
  never publish**) → MOVE to `_inbox/processed/<ts>-<name>`.
- Any pipeline failure (download/upload/copy/draft) routes via
  `continueErrorOutput` → MOVE to `_inbox/failed/<ts>-<name>`; the run returns
  `{status:"failed", file, error}` instead of leaving the file for infinite
  15-min retries. `Generate Copy` is pinned to `provider:"cloudflare"` while
  DMR is being reworked — revert to auto-routing once DMR is stable.
- Webhook body overrides: `{"platform": "...", "account_id": "...", "tone": "...", "prompt": "..."}`
  (defaults: linkedin, Cloudless company page).
- Deployed workflow id: `nextcloud-drop-to-draft`; n8n credential:
  `Nextcloud WebDAV basic` (httpBasicAuth).

## Client portals + Talk calls

- Per-client: `nextcloud-dav.py mkdir 'Client-Portals/<client>'` →
  `share --label 'Cloudless deliverables'` → send URL (wire to EspoCRM
  contact notes). Folder shares support `publicUpload=true` for a file-drop
  inbox if ever needed.
- Talk consultations: `docker exec -u www-data nextcloud php occ talk:room:create`
  or UI → set room guest-accessible → share the public link on
  cloudless.gr/contact as the "no-account video call" option. Talk HPB +
  STUN verified working; restrictive-NAT clients may need TURN (CGNAT
  limitation documented in omv-nextcloud-ops).
