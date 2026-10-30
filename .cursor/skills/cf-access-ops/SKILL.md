# cf-access-ops

Cloudflare Access (Zero Trust) operations for `*.cloudless.gr` — the SSO
gate that sits in front of every omv-tunnel hostname (ntfy, kuma, grafana,
meili, docs, espocrm, n8n, postiz, omv, webmail, ftp, manage, pi-origin,
appflowy) while public hosts (apex, social, cloud, signal) are exempt.

## When to use

- A machine client gets a 302 + `Www-Authenticate: Cloudflare-Access`
  instead of the service (ntfy app, watchdog POSTs, uptime checks)
- Decide whether an endpoint is Access-gated vs genuinely down
- Unblock a machine path: service-token policy, bypass policy, or
  tailnet/LAN fallback

## State (verified 2026-10-29)

- ZT org: `cloudless-gr.cloudflareaccess.com` — **lives on a different
  Cloudflare account** than `Baltzakis.themis@gmail.com's Account`
  (`fb7dc7b69b662480cd5961a4d1913c78` — shows "need an active plan" for ZT).
  Managing the apps needs the other account's dashboard login.
- `CLOUDFLARE_API_TOKEN` (social-api env): valid, sees the zone + tunnels,
  but NOT the ZT org (`access/groups` → 10000 auth error; apps/policies
  read as empty).

## Tool

`scripts/cf_access_audit.py`:

```bash
python cf_access_audit.py probe ntfy.cloudless.gr kuma.cloudless.gr
python cf_access_audit.py scan            # probes all known tunnel hosts
python cf_access_audit.py orgs            # what the API token can see
python cf_access_audit.py policy ntfy.cloudless.gr   # emits bypass-policy JSON
```

`policy` emits a ready-to-apply **bypass** Access policy (`include: everyone`,
`action: bypass`) — add it to the app's policies in Zero Trust → Access →
Applications when logged into the owning account, or apply via API with a
permitted token. A **service-token** alternative (machine clients send
`CF-Access-Client-Id/Secret` headers) is emitted with `--service-token` —
but the ntfy Android app can't send custom headers, so bypass is the right
fix for phone-facing endpoints.

## Rules

- Prefer tailnet/LAN endpoints for internal publishers (zero exposure).
- Only bypass what must be machine/public-reachable (ntfy push topics,
  webhook receivers). ntfy topics stay protected by their own ACLs.
- Never weaken Access on admin UIs (grafana/espocrm/n8n/kuma stay gated).
