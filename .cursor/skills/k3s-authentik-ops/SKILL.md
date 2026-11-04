# k3s-authentik-ops

Operate the authentik SERVER workload on the omv-ha k3s cluster (auth.cloudless.gr → cloudflared-omv → `http://192.168.1.128:30910` NodePort). Companion to `authentik-ops` (admin API calls) and `omv-ops` (host ops). Use to mint the admin API token, apply the blueprint, and stand up the tailnet admin plane.

## Access ladder (2026-11-04 findings)

- SSH: `ssh tbaltzakis@100.95.117.84` over Tailscale (per global rule — never LAN IP).
- `kubectl` as user tbaltzakis: kubeconfig exists (`~/.kube/config`) but calls returned empty/hung — verify cluster health first: `systemctl status k3s`.
- `sudo` needs a password — non-interactive calls fail; use `sudo -S` with the password piped from a secret-store env var (`OMV_SUDO_PASS` — never hardcode/print), or run interactively.
- Tagged nodes (ingress-0, k3s-subnet-router-0) accept no user SSH.
- authentik is a **k3s workload**, NodePort 30910 on 192.168.1.128 — `docker exec` won't reach it; use `k3s kubectl exec`.

## Token mint (once kubectl works)

`scripts/mint-admin-token.sh` — finds the authentik pod, runs
`ak create_token --user akadmin --identifier devin-agent --expiring=false`,
writes `AUTHENTIK_ADMIN_TOKEN=<tok>` to the project `.env` (token never printed).

## Full apply

`scripts/apply.sh` — end-to-end: k3s health check → locate workload → mint token →
update_app_meta.py → apply `omv-infrastructure/omv/authentik/blueprint.yaml` via
admin API → print remaining manual gates (CF Access tailnet policy, tailnet ACLs,
`tailscale serve` for /if/admin). Each step idempotent + skips when done.
