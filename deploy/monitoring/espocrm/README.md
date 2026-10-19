# EspoCRM monitoring

Prometheus exporter + Grafana dashboard for the self-hosted EspoCRM ERP
(`espocrm` namespace on the omv k3s cluster).

EspoCRM (`espocrm/espocrm:9`, PHP + MariaDB) ships no `/metrics` endpoint, so
`exporter.py` queries the MariaDB schema directly for the vitals that matter:
leads by status/source, per-TargetList membership, campaigns, stream notes,
the scheduled-job queue (`espocrm_jobs` + `espocrm_job_failures_24h`), email
volume, active users, and DB size. Metrics are rebuilt on each scrape so stale
label sets never linger.

## Layout

```
espocrm/           Deployment + Service (namespace: espocrm)
monitoring/        ServiceMonitor "espocrm" (namespace: monitoring)
```

## Deploy (on omv, via Tailscale)

```sh
cd deploy/monitoring/espocrm
./apply.sh
```

Credentials: `apply.sh` provisions a dedicated **read-only** MariaDB user
(`metrics_reader`, SELECT on `espocrm.*` only) using the
`espocrm-secrets/mariadb-root-password` key when present, and stores the
credentials in the `espocrm-exporter-db` secret — the exporter never holds
the app's write-capable password unless root credentials are unavailable
(fallback prints a warning).

Access control: `/metrics` requires `Authorization: Bearer <token>`
(`metrics-token` in `espocrm-exporter-db`); the ServiceMonitor reads the same
token from `espocrm-exporter-metrics-token` in the `monitoring` namespace.
Only `/healthz` (kubelet probes) is unauthenticated. Each request builds a
fresh registry, so a failed scrape yields just `espocrm_up 0` — never stale
lead/campaign numbers.

After rollout, Prometheus target `espocrm` appears under
Status → Targets and the "EspoCRM — Vitals" dashboard auto-loads in Grafana
(ConfigMap label `grafana_dashboard=1`, sidecar pick-up ~30s).
