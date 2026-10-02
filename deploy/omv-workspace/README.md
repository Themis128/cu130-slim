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

## Public endpoints (via `social-cloudflared` tunnel on cu130)

- `https://cloud.cloudless.gr` → `http://192.168.1.200:11000` (Nextcloud)
- `https://office.cloudless.gr` → `http://192.168.1.200:9980` (Collabora)

Tunnel ingress is configured on the Cloudflare side (tunnel
`2efdd26e-8379-4b7b-ba7f-f4e6c19e0db3`); DNS CNAMEs for both hostnames point at
the tunnel and are proxied.

## Deploy / update on omv

```bash
ssh -i ~/.ssh/id_rsa_win tbaltzakis@192.168.1.200
cd ~/workspace
docker compose pull && docker compose up -d
```

Credentials live in `~/workspace/.env` on omv (chmod 600, never committed):
`NEXTCLOUD_DB_*`, `NEXTCLOUD_ADMIN_*`, `COLLABORA_ADMIN_PASSWORD`.

## Installed apps

`richdocuments` (Collabora WOPI), `calendar`, `contacts`, `spreed` (Talk) —
all enabled. Collabora: `wopi_url=http://collabora:9980`,
`public_wopi_url=https://office.cloudless.gr`.

## Outgoing mail

Nextcloud sends via the omv-ha relay (`192.168.1.130:587`, STARTTLS + SASL,
`tbaltzakis@cloudless.gr` → Resend). Same mail stack documented in
`.cursor/skills/omv-ha-mail/SKILL.md`.

## Backups

- Data dirs under the 1TB SSD `workspace/` path — back up with the existing
  OMV backup jobs (or `rsync`/restic to another disk).
- DB dumps: `docker exec nextcloud-db pg_dump -U nextcloud nextcloud`.

## Headroom notes

k3s + monitoring (~800Mi) + espocrm + uptime-kuma remain on omv. Dead
workloads were scaled to 0 on 2026-10-01 (postiz, appflowy, k3s n8n,
headlamp) freeing ~2 GB. If RAM gets tight, check
`k3s kubectl top pods -A` first.
