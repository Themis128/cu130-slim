---
description: >
  Operates the self-hosted workspace stack on the omv node (Nextcloud 34,
  Collabora, Talk HPB, notify-push, cron, redis, postgres) including external
  connectivity through Cloudflare Tunnel, Talk signaling/STUN/TURN health, CGNAT
  limitations, storage layout, backups, and the live-vs-repo compose workflow.
  Use when checking cloud.cloudless.gr, office.cloudless.gr,
  signal.cloudless.gr, Nextcloud Talk calls, cron/backups, container health on
  omv, or compose changes under deploy/omv-workspace/.
---

# omv Nextcloud workspace ops

## Node and stack

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

## Public access (CGNAT-safe)

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

## Storage layout (verified 2026-10)

- **1 TB SSD** (`/srv/dev-disk-by-uuid-fa6231ab-…`, sdb1): all irreplaceable
  state — `workspace/nextcloud-data`, `nextcloud-db`, `nextcloud-html`,
  `appapi-certs`, `Backups/daily-snapshot/workspace`.
- **120 GB drive** (sda1): Docker named volumes for `nextcloud-redis` and
  `talk-recording` scratch `/tmp` — rebuildable, deliberately not migrated.
- Root/eMMC (`mmcblk0p2`, 59 GB): OS only.

## Health-check commands

```bash
ssh -i ~/.ssh/id_rsa_win tbaltzakis@192.168.1.200 \
  "docker ps --format '{{.Names}} {{.Status}}' | grep nextcloud"
# occ inside the container:
docker exec -u www-data nextcloud php occ status
docker exec -u www-data nextcloud php occ background:job list --limit 5 \
  | python3 -c "import sys,json; [print(j['last_run']) for j in map(json.loads,sys.stdin)]"
# cron really running = fresh last_run timestamps (minutes, not hours)
# backups:
ls -la /srv/dev-disk-by-uuid-fa6231ab-*/Backups/daily-snapshot/workspace
# external:
curl -sI https://cloud.cloudless.gr/status.php        # expect 200
curl -sI https://cloud.cloudless.gr/remote.php/dav    # expect 401 challenge
```

## Talk connectivity (verified 2026-10)

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

## Known quirks

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
