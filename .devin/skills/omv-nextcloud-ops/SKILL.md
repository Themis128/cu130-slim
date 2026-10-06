---
name: omv-nextcloud-ops
description: >-
  Operate the self-hosted Nextcloud workspace on omv (Nextcloud 34, Collabora, Talk HPB, notify-push, cron, redis, postgres, Cloudflare Tunnel) plus the SocialAuto↔Nextcloud integration paths. Use for cloud/office/signal.cloudless.gr, Talk calls, or workspace compose.
---

# Omv Nextcloud Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| omv Nextcloud workspace ops | `omv-nextcloud-ops` |
| nextcloud-integration | `omv-nextcloud-ops` → `nextcloud-integration/` |

## omv Nextcloud workspace ops

### Node and stack

- **omv node**: `192.168.1.200`, aarch64, 8 GB RAM, OpenMediaVault + k3s.
  SSH: `ssh -i ~/.ssh/id_rsa_win tbaltzakis@192.168.1.200` (key auth,
  `id_rsa_win`); sudo is passwordless (`sudo -n`). Do **not** confuse with
  omv-ha (`192.168.1.130`, 1 GB Pi — mail only, never move workloads there).
- **Live compose project** (OMV plugin-managed):
  `/srv/dev-disk-by-uuid-fa6231ab-eae7-40ea-a4b6-400f767a89d7/compose-files/workspace/`
  with `workspace.yml` (generated), `compose.yml`, `compose.override.yml`.
  Changes go into `workspace.yml` directly or `compose.override.yml`
  (override survives plugin regeneration); a GUI save can regenerate
  `workspace.yml`. Repo copy: `deploy/omv-workspace/docker-compose.yml` —
  keep it in sync via PR.
- **Containers**: `nextcloud` (:11000→80), `nextcloud-db`, `nextcloud-redis`,
  `nextcloud-cron`, `collabora` (:9980), `nextcloud-talk-hpb` (TCP+UDP 3478,
  signaling on host port 8090), `nextcloud-talk-recording`, `nextcloud-appapi-harp`,
  `nextcloud-notify-push` (:7867).
- Admin credentials for all services follow the standing convention
  (see global rules — never print them).

### Public access (CGNAT-safe)

- **Cloudflare Tunnel** is the only public ingress — WAN is CGNAT
  (`100.64.0.0/10`), so router port-forwards cannot work.
- Dedicated tunnel **`omv-cloudless`** (`a474360d`) — the `cloudflared-omv`
  docker service on omv (compose + token in `~/cloudflared/`, repo mirror
  `deploy/omv-workspace/cloudflared/`). Config is **remotely managed** on the
  Cloudflare side. It was split off `social-cloudless` (2026-10-05): remote
  tunnels share ONE ingress across all replicas, so an omv replica of the
  social tunnel served social.cloudless.gr/api as 502 (`social-api` doesn't
  resolve there). Never join a replica to either tunnel that can't resolve
  every origin in that ingress.
- Routes: `cloud.cloudless.gr` → Nextcloud, `office.cloudless.gr` → Collabora,
  `signal.cloudless.gr` → Talk HPB signaling, `push.cloudless.gr` → notify-push.

### Storage layout (verified 2026-10)

- **1 TB SSD** (`/srv/dev-disk-by-uuid-fa6231ab-…`, sdb1): all irreplaceable
  state — `workspace/nextcloud-data`, `nextcloud-db`, `nextcloud-html`,
  `appapi-certs`, `Backups/daily-snapshot/workspace`.
- **120 GB drive** (sda1): Docker named volumes for `nextcloud-redis` and
  `talk-recording` scratch `/tmp` — rebuildable, deliberately not migrated.
- Root/eMMC (`mmcblk0p2`, 59 GB): OS only.

### Health-check commands

```bash
ssh -i ~/.ssh/id_rsa_win tbaltzakis@192.168.1.200 \
  "docker ps --format '{{.Names}} {{.Status}}' | grep nextcloud"
## occ inside the container:
docker exec -u www-data nextcloud php occ status
docker exec -u www-data nextcloud php occ background:job list --limit 5 \
  | python3 -c "import sys,json; [print(j['last_run']) for j in map(json.loads,sys.stdin)]"
## cron really running = fresh last_run timestamps (minutes, not hours)
## backups:
ls -la /srv/dev-disk-by-uuid-fa6231ab-*/Backups/daily-snapshot/workspace
## external:
curl -sI https://cloud.cloudless.gr/status.php        # expect 200
curl -sI https://cloud.cloudless.gr/remote.php/dav    # expect 401 challenge
```

### Talk connectivity (verified 2026-10)

Three separate pieces — do not conflate:

| Piece | Endpoint | Check |
|---|---|---|
| **Signaling (HPB)** | `signal.cloudless.gr` | `curl https://signal.cloudless.gr/api/v1/welcome` → 200 `{"nextcloud-spreed-signaling":"Welcome",…}`. Root `/` 404 is **normal** (Go service). |
| **STUN** | `stun.nextcloud.com:443`, `stun.cloudflare.com:3478` | UDP binding probe — `scripts/talk-check.py` does this. |
| **TURN** | `192.168.1.200:3478` (eturnal, LAN only) | Answers STUN probes on LAN; **unreachable externally** — CGNAT blocks inbound. |

- **CGNAT reality**: router port-forward exists but WAN `100.80.x.x` is carrier
  NAT. Only fixes: ISP-provided public IPv4 (ask — usually free in GR), or a
  publicly reachable TURN relay.
- **Free hosted TURN status (2026-10-02)**: Metered's Open Relay
  `staticauth.openrelay.metered.ca` is **dead** (all TCP/UDP ports time out;
  the project moved to dashboard-issued rotating credentials that Nextcloud's
  static-secret `occ talk:turn:add` config cannot express). Do not re-add it.
- **Impact without external TURN**: Talk works for most NAT types via STUN
  (direct P2P) and all LAN/WiFi calls via local TURN; only participants behind
  symmetric/restrictive NATs fail. Per official docs, TURN is the last-resort
  relay for those cases.
- TURN on port 443 is the official recommendation for restrictive client
  firewalls — but requires a public IP we don't have under CGNAT.

`occ` config checks:

```bash
docker exec -u www-data nextcloud php occ talk:stun:list
docker exec -u www-data nextcloud php occ talk:turn:list
docker exec -u www-data nextcloud php occ talk:signaling:list
```

`scripts/talk-check.py` runs the external probes (signaling welcome + real
STUN binding requests) and prints an honest pass/fail summary.

### Known quirks

- **Fontconfig tmpfs** (PR #244, live on omv): `/var/cache/fontconfig` was
  root-owned → `www-data` warnings + per-request font rescans. `tmpfs` mounts
  on `nextcloud`, `nextcloud-cron`, `notify-push` fix it. If the warning
  reappears after a `workspace.yml` regeneration, re-apply.
- `.well-known/caldav` 301s to `http://` (Apache `.htaccess` static redirect
  ignores `overwriteprotocol`) — cosmetic, Cloudflare upgrades to HTTPS.
- **System-dir tmpfs masks** (PR #271/#273, live on omv): six dirs under
  `/omv-storage` (`omv-ai-cluster`, `k3s-data`, `workspace`,
  `compose-files`, `tempo-data`, `lost+found`) are masked by empty tmpfs
  overlays so DAV never lists/writes them and containerd symlink trees
  stop logging `Following symlinks is not allowed`. `lost+found` uses a
  long-syntax `type: tmpfs` volume with `mode: 0o755` — plain `tmpfs:`
  entries inherit the underlying dir's `2770 root:root` and resurface a
  scan `Permission denied`. Regeneration drops them; re-apply.
- **Stale filecache purge**: when masking/hiding dirs that were already
  indexed, `occ files:scan` can fail to remove stale rows — the scanner's
  bulk delete hits PostgreSQL's 65535-parameter cap and rolls back
  (`Removed: 0`, log shows `number of parameters must not exceed 65535`).
  Fallback that worked: chunked SQL
  `DELETE FROM oc_filecache WHERE fileid IN (SELECT fileid FROM
  oc_filecache WHERE storage=<ext-storage id> AND path LIKE '<dir>/%'
  LIMIT 30000)` looped to zero (deleted ~364k rows). Find the storage id
  via `oc_storages` (`local::/omv-storage/` = 5); filecache paths are
  storage-relative, no `files/` prefix.
- Recreated containers = ~40 s Nextcloud blip; fine for ops work.

## nextcloud-integration

Integrate the omv Nextcloud workspace (`cloud.cloudless.gr`, NC 34) with
SocialAuto, n8n workflows, and cloudless.gr. Covers WebDAV ops, OCS public
shares, the n8n nextCloud node pitfalls, drop-folder automation, and the
canonical credential layout.

For stack ops (containers, Talk, tunnels, storage) see `omv-nextcloud-ops`.

### Credentials — app password only

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

### Folder layout (workspace convention)

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

### `scripts/nextcloud-dav.py` — the DAV tool

```bash
python3 .devin/skills/omv-nextcloud-ops/nextcloud-integration/scripts/nextcloud-dav.py \
  list SocialAuto/media | upload ./a.png SocialAuto/media/a.png | \
  move a b | mkdir dir | delete path | download r l | share path [--label L] [--expire YYYY-MM-DD] [--password P]
```

- `share` prints the public URL (`https://cloud.cloudless.gr/index.php/s/<token>`).
  Always sends explicit `permissions=1` (read-only): Nextcloud has a bug where
  omitted permissions apply a wrong mask (GH nextcloud/server#37774).

### WebDAV notes that bit us (verified 2026-10)

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

### n8n integration — DO NOT use the nextCloud node for path-sensitive ops

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

### SocialAuto backend integration (shipped)

- `app/services/nextcloud_export.py` — mirrors every `media_assets` row to
  `<root>/media/<storage_path>` via WebDAV MKCOL+PUT; records
  `meta_data.nextcloud_path`; `create_public_share()` wraps the OCS call.
- `app/worker/tasks/nextcloud.py::export_media_to_nextcloud` — media queue,
  enqueued from `media_storage.py` alongside auto-tag when
  `NEXTCLOUD_EXPORT_ENABLED=true`. Never raises into the request path.
- Manual export of an existing asset:
  `docker exec social-worker-media celery -A app.worker.celery_app call app.worker.tasks.nextcloud.export_media_to_nextcloud --args='["<asset_uuid>"]'`

### Drop-folder → draft workflow (n8n `nextcloud-drop-to-draft`)

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

### Client portals + Talk calls

- Per-client: `nextcloud-dav.py mkdir 'Client-Portals/<client>'` →
  `share --label 'Cloudless deliverables'` → send URL (wire to EspoCRM
  contact notes). Folder shares support `publicUpload=true` for a file-drop
  inbox if ever needed.
- Talk consultations: `docker exec -u www-data nextcloud php occ talk:room:create`
  or UI → set room guest-accessible → share the public link on
  cloudless.gr/contact as the "no-account video call" option. Talk HPB +
  STUN verified working; restrictive-NAT clients may need TURN (CGNAT
  limitation documented in omv-nextcloud-ops).
