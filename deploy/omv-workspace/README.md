# Self-hosted workspace on `omv` (replaces M365 / Google Workspace)

Runs on the **omv node** (`192.168.1.200`, aarch64, 8 GB RAM, k3s host) — NOT on
cu130. All persistent data lives on the 1 TB USB SSD
(`/srv/dev-disk-by-uuid-fa6231ab-eae7-40ea-a4b6-400f767a89d7/workspace/`).

## Services

| Container | Role | Port (host) |
|---|---|---|
| `nextcloud` | Files + Calendar/Contacts (CalDAV/CardDAV) + Talk + dashboard | `11000 → 80` |
| `nextcloud-db` | PostgreSQL 16 | internal |
| `nextcloud-cron` | Background jobs (file scan, previews, cleanup) | — |
| `nextcloud-redis` | File locking + distributed cache | internal |
| `collabora` | Collabora Online (Docs/Sheets/Slides) | `9980` |
| `talk-hpb` (`aio-talk`) | Talk HPB: signaling + Janus SFU + eturnal TURN | `8090`, `3478 tcp/udp` |
| `talk-recording` (`aio-talk-recording`) | Talk call recording backend | internal `1234` |
| `nextcloud-notify-push` | Client Push daemon (instant Talk/file notifications) | `7867` |
| `nextcloud-appapi-harp` | AppAPI deploy daemon (HaRP, replaces deprecated DSP) | internal `8780/8782` |

## Public endpoints (via dedicated `omv-cloudless` tunnel on omv)

- `https://cloud.cloudless.gr` → `http://192.168.1.200:11000` (Nextcloud)
- `https://office.cloudless.gr` → `http://192.168.1.200:9980` (Collabora)
- `https://signal.cloudless.gr` → `http://192.168.1.200:8090` (Talk signaling, wss)
- `https://push.cloudless.gr` → `http://192.168.1.200:7867` (notify_push endpoint, clients connect to `/push`)

`NEXTCLOUD_URL=http://nextcloud` on notify-push bypasses the tunnel for its
callback — required so the self-test sees the push server as a trusted proxy.

Tunnel ingress is remotely managed on the Cloudflare side (tunnel
`a474360d-b7a5-4677-9c33-788adaf1f2ed`, name `omv-cloudless`); the four DNS
CNAMEs point at it. The `cloudflared-omv` container runs it locally —
compose + token live in `~/cloudflared/` on omv (repo mirror:
[`cloudflared/docker-compose.yml`](cloudflared/docker-compose.yml)).

**Do not join a second replica to this tunnel** unless it can resolve every
ingress origin — remote-managed tunnels share one config across all
replicas. The same rule applies in reverse: omv hostnames were split off
the `social-cloudless` tunnel (2026-10-05) because an omv replica of it
served `social.cloudless.gr/api` with 502s (`social-api` doesn't resolve
there).

## Deploy / update on omv

The stack is managed natively by the **openmediavault-compose plugin**
(OMV 8.1.1 + plugin 8.1.28):

- Compose file registered as `workspace` (uuid `ccfe0681-8481-456d-95cb-9a3624a0f6e1`)
- Plugin storage: shared folder `compose-files` →
  `/srv/dev-disk-by-uuid-fa6231ab-eae7-40ea-a4b6-400f767a89d7/compose-files/workspace/`
  (`workspace.yml` + `workspace.env` + `compose.override.yml`)
- Data shared folder `workspace` → `/srv/.../workspace/` (all volumes)
- Manage via OMV web UI → Services → Compose → Files, or CLI:

```bash
ssh -i ~/.ssh/id_rsa_win tbaltzakis@192.168.1.200
D=/srv/dev-disk-by-uuid-fa6231ab-eae7-40ea-a4b6-400f767a89d7/compose-files/workspace
sudo docker compose -f $D/workspace.yml -f $D/compose.override.yml \
  --env-file $D/workspace.env up -d
```

OMV workbench: **`http://192.168.1.200:9080`** (moved off :80 — k3s/Traefik
claims IPv4 :80; a ufw rule allows :9080 from `192.168.1.0/24`). OMV RPC is
available via `http://[::1]/rpc.php` with `admin` credentials for automation.

Credentials live in `workspace.env` on omv (managed by the plugin, never
committed): `NEXTCLOUD_DB_*`, `NEXTCLOUD_ADMIN_*`, `COLLABORA_ADMIN_PASSWORD`,
`TALK_TURN_SECRET`, `TALK_SIGNALING_SECRET`, `TALK_INTERNAL_SECRET`,
`TALK_RECORDING_SECRET`, `APPAPI_HARP_KEY`.

## Talk / calls

- HPB: `aio-talk` (signaling + Janus SFU + eturnal TURN) → `signal.cloudless.gr`
- TURN server registered: `turn,turns 192.168.1.200:3478` (LAN only).
  **For calls with external participants, port-forward UDP+TCP 3478 → omv on
  the router** — Cloudflare tunnel cannot carry UDP media.
- Recording backend registered at `http://talk-recording:1234`
  (`spreed.recording_servers` app config).
- SIP dial-in intentionally not configured — requires a paid SIP trunk.

## Installed apps

Core workspace: `richdocuments` (Collabora WOPI), `calendar`, `contacts`,
`spreed` (Talk), `mail`, `deck`, `tasks`, `notes`, `collectives` (wiki),
`tables`, `forms`, `polls`, `passwords` (team credential store),
`announcementcenter`, `groupfolders`, `files_external`, `files_pdfviewer`,
`viewer`, `text`, `photos`, `previewgenerator`.
Security/ops: `twofactor_totp` (enforced), `twofactor_nextcloud_notification`,
`admin_audit`, `suspicious_login`, `bruteforcesettings`, `logreader`,
`notify_push`.
Disabled: `files_rightclick` (incompatible with NC 34), `encryption`,
`user_ldap`.

Collabora: `wopi_url=http://collabora:9980`,
`public_wopi_url=https://office.cloudless.gr`.
Talk signaling registered via `occ talk:signaling:add https://signal.cloudless.gr <secret>`
(secret in `workspace/signaling/server.conf` → `[backend1] secret`).

Retention policy: file versions ≤ 1 year, trash auto-purge 90 days, activity
log 365 days — keeps SSD growth bounded.

## Configured integrations

- **Mail app** → omv-ha dovecot/postfix (`mail.cloudless.gr`, imaps:993 +
  submission:587, account `tbaltzakis@cloudless.gr`). TLS verification is ON:
  the self-signed `CN=mail.cloudless.gr` cert is imported into Nextcloud's
  certificate store (`occ security:certificates:import`, valid → Nov 2028)
  and `extra_hosts` pins `mail.cloudless.gr → 192.168.1.130` inside the
  nextcloud container (public DNS resolves to the WAN IP, unreachable for
  IMAP). Mailbox password aligned to the unified admin credential.
- **External storage** → `OMV Storage` **Local** mount (id 2) exposing the
  1 TB SSD inside Files via the `/omv-storage` bind mount (replaced the
  self-SFTP mount — phpseclib logins intermittently failed under PROPFIND
  fan-out, producing 10s timeouts and 5xx on ~9% of cloud.cloudless.gr
  traffic). Permission model: the SSD data dirs are `tbaltzakis:users` with group
  `rwx` + setgid, plus POSIX ACLs `g:www-data:rwX` (access) and
  `d:g:www-data:rwX` + `d:g:users:rwX` (defaults). The `g:33` ACL is the
  operative entry for Apache — its workers drop `group_add` supplementary
  groups via `initgroups()` and run with `Groups: 33` only; `group_add:
  100` is kept for `occ`/exec paths. System subtrees (`k3s-data`,
  `Backups`, `workspace`, `compose-files`, `tempo-data`) are root-owned
  and intentionally not group-writable. Six of them (`omv-ai-cluster`,
  `k3s-data`, `workspace`, `compose-files`, `tempo-data`, `lost+found`)
  are additionally masked by empty tmpfs overlays inside all three
  Nextcloud containers, so DAV never lists or writes them — this also
  silences the `Following symlinks is not allowed` level-3 spam from
  containerd's symlink-heavy snapshot tree. Caveat: `files:scan` hit the
  Postgres 65535-parameter ceiling trying to bulk-purge the ~364k stale
  `oc_filecache` rows those trees had accumulated; the rows were deleted
  with a chunked SQL `DELETE` instead (the documented fallback when the
  scanner's own purge rolls back). A GUI compose regeneration drops the
  tmpfs masks — re-apply from this file if symlink errors return.
- **Branding** → theming app: name `Cloudless`, slogan "Clear skies. Zero
  friction.", color `#0a7785`, Cloudless wordmark/icon/favicon from
  `cloudless.gr/BRANDING/cloudless-brand/`.
- **Defaults** → `default_phone_region=GR`; link shares get a 30-day default
  expiry (not enforced); activity digest daily; availability Mon–Fri 09:00–18:00
  Europe/Athens (free/busy); lookup-server data sharing off.

## Outgoing mail

Nextcloud sends via the omv-ha relay (`192.168.1.130:587`, STARTTLS + SASL,
`tbaltzakis@cloudless.gr` → Resend). Same mail stack documented in
`.cursor/skills/omv-ha-mail/SKILL.md`.

## Security / accounts

- **2FA (TOTP) is enforced instance-wide** — every user must enroll at next
  login. Admin account `tbaltzakis@cloudless.gr` is enrolled.
- Recovery material (TOTP secret, otpauth URI, QR, 10 backup codes):
  `RECOVERY_SECRETS.txt` + `TOTP-QR.png` in this dir — **gitignored, never
  commit**; a copy also lives on the omv SSD (`workspace/RECOVERY_SECRETS.txt`,
  mode 600).
- Profile avatar = Cloudless logo (uploaded via `POST /avatar/` — the official
  endpoint; hand-writing `appdata_*/avatar/` files fights the generated-avatar
  marker + cached resizes and silently serves stale initials).

## Mobile onboarding

See [`MOBILE_SETUP.md`](./MOBILE_SETUP.md) — Nextcloud/Talk apps, photo
auto-upload, DAVx⁵ (Android) and app-password CalDAV/CardDAV (iOS).

## Backups

`/usr/local/sbin/nas-backup` (nightly 02:00) now includes a workspace section —
verified 2026-10-02, ~191 MB per run:

- `pg_dump` of the `nextcloud` DB → `nextcloud-db.sql.gz`
- `nextcloud-data/` (files, appdata, avatars)
- `nextcloud-config/` (from `nextcloud-html/config`)
- `branding/`, `signaling/`, `appapi-certs/`, `RECOVERY_SECRETS.txt`

Written to **both** the same-disk `Backups/` snapshot and the cross-disk
`NAS-Backup/workspace/` (second disk `a9a5a108-…`). Manual run:

```bash
docker exec nextcloud-db pg_dump -U nextcloud nextcloud   # DB only
sudo /usr/local/sbin/nas-backup                          # full nightly job
```

## Host-level services (outside the workspace compose)

WeTTY and Filebrowser run as separate OMV-compose pods, not in
`workspace.yml` — document the live host config here so a rebuild doesn't
lose it.

### WeTTY (web SSH terminal, :2222)

- Pod units: `pod-wetty`, `wetty-app`, `wetty-proxy` (systemd). Caddy inside
  the pod proxies `:8443 → wetty:3000` with `auto_https off` — test with
  **plain HTTP**: `curl -s http://127.0.0.1:2222` (HTTPS probes report 000).
- **After every wetty image update**: newer images crash-loop with
  `Configure at least one allowed origin` unless `allowedOrigins` is set.
  The fix lives in a systemd drop-in
  `/etc/systemd/system/wetty-app.service.d/allowed-origins.conf`
  (`Environment=ALLOWEDORIGINS=...` listing the LAN name, tailscale name and
  `http://192.168.1.200:2222`). If `wetty-app` exits in <1s after an update,
  check this drop-in first.

### Filebrowser (:3670)

- Pod units: `pod-filebrowser`, `filebrowser-app`.
- **UID trap**: the container runs as `filebrowser` **uid 1000**, but the
  host `filebrowser` account is **uid 994**. Bind-mounted
  `/var/lib/filebrowser` must be owned `1000:1000` or the app crash-loops on
  `could not open database: permission denied`.

### openmediavault-writecache tmpfs

`/run/omv-writecache` is a 2 GB tmpfs (25% of RAM) holding overlay uppers for
`/var/log`, apt caches, `/var/lib/dpkg/updates`, rrd, monit, samba. When it
fills, failures show up far away: dpkg `No space left`, rsyslog
write-error storms, apt install breakage — while the root disk looks fine.

- **Cause of the 2026-10-10 fill**: `daemon.log` had *no* logrotate coverage
  (the rsyslog logrotate.d file only lists syslog/mail/kern/auth/user/cron)
  and grew to 1.1 GB via k3s/systemd churn + pod crash-loops.
- Fix deployed: `/etc/logrotate.d/omv-writecache-logs`
  (`daemon/syslog/user/kern/ufw/pi-alert-remediation`, `maxsize 40M`,
  rotate 2) + `/etc/cron.d/logrotate-writecache` (hourly enforcement —
  daily `cron.daily` runs are too slow for a runaway logger).
- Quick health: `df -h /run/omv-writecache` — if >80%, check
  `du -sh /run/omv-writecache/*/upper | sort -rh` for the offender.
- `tmpfs_size` lives in `/etc/omv-writecache/config.yaml` (25%) — raise it
  there rather than fighting log growth if RAM headroom allows.

### Rootless podman

`uidmap` is installed (provides `newuidmap`/`newgidmap`); `tbaltzakis` has
`subuid/subgid 100000:65536`. Rootless `podman` works for the tbaltzakis
user — the pod units above run as root regardless.

### Host cron scripts

`/etc/cron.weekly/nas-maintenance` (apt/fsck/SMART/logs/samba/backup/network
report → email) is versioned at `host-scripts/nas-maintenance` in this dir —
deploy it after edits (`scp` to `/etc/cron.weekly/`, `chmod 755`). Gotchas
fixed 2026-10-10: `apt upgrade` needs `--with-new-pkgs` or docker-ce/OMV
packages get kept back; SMART/pending attrs are absent behind the USB
bridges (print `n/a`, don't leave blank); ping's `packet loss` field index
was off by one (`received%`); fail2ban `Total banned` is colon+TAB separated.

## Headroom notes

k3s + monitoring (~800Mi) + espocrm + uptime-kuma remain on omv. Dead
workloads were scaled to 0 on 2026-10-01 (postiz, appflowy, k3s n8n,
headlamp) freeing ~2 GB. If RAM gets tight, check
`k3s kubectl top pods -A` first.
