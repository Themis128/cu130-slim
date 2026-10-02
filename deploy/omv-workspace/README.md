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

`richdocuments` (Collabora WOPI), `calendar`, `contacts`, `spreed` (Talk),
`groupfolders`, `twofactor_totp` (2FA enforced for all users), `notify_push` —
all enabled. Collabora: `wopi_url=http://collabora:9980`,
`public_wopi_url=https://office.cloudless.gr`.
Talk signaling registered via `occ talk:signaling:add https://signal.cloudless.gr <secret>`
(secret in `workspace/signaling/server.conf` → `[backend1] secret`).

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

- Data dirs under the 1TB SSD `workspace/` path — back up with the existing
  OMV backup jobs (or `rsync`/restic to another disk).
- DB dumps: `docker exec nextcloud-db pg_dump -U nextcloud nextcloud`.

## Headroom notes

k3s + monitoring (~800Mi) + espocrm + uptime-kuma remain on omv. Dead
workloads were scaled to 0 on 2026-10-01 (postiz, appflowy, k3s n8n,
headlamp) freeing ~2 GB. If RAM gets tight, check
`k3s kubectl top pods -A` first.
