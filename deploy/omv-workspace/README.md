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

## Public endpoints (via `social-cloudflared` tunnel on cu130)

- `https://cloud.cloudless.gr` → `http://192.168.1.200:11000` (Nextcloud)
- `https://office.cloudless.gr` → `http://192.168.1.200:9980` (Collabora)
- `https://signal.cloudless.gr` → `http://192.168.1.200:8090` (Talk signaling, wss)
- `https://push.cloudless.gr` → `http://192.168.1.200:7867` (notify_push endpoint, clients connect to `/push`)

`NEXTCLOUD_URL=http://nextcloud` on notify-push bypasses the tunnel for its
callback — required so the self-test sees the push server as a trusted proxy.

Tunnel ingress is configured on the Cloudflare side (tunnel
`2efdd26e-8379-4b7b-ba7f-f4e6c19e0db3`); DNS CNAMEs for both hostnames point at
the tunnel and are proxied.

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

## Headroom notes

k3s + monitoring (~800Mi) + espocrm + uptime-kuma remain on omv. Dead
workloads were scaled to 0 on 2026-10-01 (postiz, appflowy, k3s n8n,
headlamp) freeing ~2 GB. If RAM gets tight, check
`k3s kubectl top pods -A` first.
