# Nextcloud client portals + Talk consultations

Client-facing use of the self-hosted workspace on the omv node —
`cloud.cloudless.gr` (Nextcloud 34) for per-client file portals and
Nextcloud Talk for consultations. Zero SaaS cost; data never leaves the
OMV 1TB SSD.

## Client portal folders

Standard layout under `/Client-Portals/` (created for every client):

```
Client-Portals/
  <client-slug>/
    01-brief/            # intake docs, requirements
    02-deliverables/     # what we ship (reports, assets, builds)
    03-shared-from-client/  # files the client uploads for us
    99-archive/          # closed-out material
```

Create per client:

```bash
DAV=.devin/skills/omv-nextcloud-ops/nextcloud-integration/scripts/nextcloud-dav.py
SLUG=acme
python3 $DAV mkdir "Client-Portals/$SLUG"
python3 $DAV mkdir "Client-Portals/$SLUG/01-brief"
python3 $DAV mkdir "Client-Portals/$SLUG/02-deliverables"
python3 $DAV mkdir "Client-Portals/$SLUG/03-shared-from-client"
python3 $DAV mkdir "Client-Portals/$SLUG/99-archive"
```

## Sharing options

| Option | When | How |
|---|---|---|
| **Public upload link** | Client needs to send us files (no account) | `nextcloud-dav.py share "Client-Portals/$SLUG/03-shared-from-client" --permissions 4 --expire YYYY-MM-DD` — write-only, expires |
| **Public read link** | Deliver a fixed artifact | `nextcloud-dav.py share "<path>" --permissions 1` — read/download only |
| **Federated/account share** | Client has their own Nextcloud | OCS shares API `shareType=6`, or create a guest user via `occ user:add` |

Rules:
- **Always set explicit `permissions`** in the share call — Nextcloud's
  OCS API has a known quirk where omitted permissions land wrong.
- **Always set `--expire`** on upload links; review shares monthly.
- Deliverable folders stay internal — share links are created per-file
  or per-folder at delivery time, never on `Client-Portals/` root.
- Never share folders containing other clients' data — one client per
  slug, no shared parent links.

## Talk consultations

Permanent public room for sales/onboarding calls (created 2026-10-04):

- **Guest link:** `https://cloud.cloudless.gr/call/zsykkx97`
- Anyone with the link joins as guest — no account, no install
- Linked from `cloudless.gr/links` → "Book a free video consult"

Creating additional rooms (e.g. per-client recurring calls):

```bash
curl -u "$NC_USER:$NC_PASS" \
  -X POST "https://cloud.cloudless.gr/ocs/v2.php/apps/spreed/api/v4/room" \
  -H 'OCS-APIRequest: true' -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{"roomType":3,"roomName":"<Client> weekly"}'
# response .ocs.data.token -> https://cloud.cloudless.gr/call/<token>
```

`roomType=3` = public room (guest-joinable). `roomType=2` = group room
(account-required). Rotate the token by deleting + recreating the room if
a link leaks.

## Honest limits (CGNAT)

- Talk signaling (`signal.cloudless.gr`) + STUN work — calls succeed in
  most home/office NAT setups.
- **No external TURN** — a participant behind a restrictive/corporate NAT
  or symmetric NAT may fail to connect. Fallback for those rare cases:
  send them a phone-bridge alternative or reschedule; do NOT stand up a
  paid TURN service.
- Local TURN works on the LAN for internal calls.
- Talk recording container exists but recording is off by default —
  ask the client before recording anything.

## Backups & retention

- Workspace data lives on the OMV 1TB SSD; daily snapshots under
  `Backups/daily-snapshot/workspace` cover the Nextcloud data dir.
- `files_versions` keeps file history — overwrites (e.g. the rolling
  `automation-checklist-latest.pdf`) are recoverable.
- On offboarding a client: move their slug to `Client-Portals/_closed/`,
  delete any live share links (`OCS DELETE /shares/<id>`), and archive
  per the engagement contract.

## Related

- `.devin/skills/omv-nextcloud-ops/SKILL.md` — WebDAV paths, app
  passwords, OCS share API, n8n node quirks
- `.devin/skills/omv-nextcloud-ops/SKILL.md` — stack health, TURN/STUN,
  backups, compose layout
