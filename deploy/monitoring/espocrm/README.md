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

Credentials: the exporter reuses `espocrm-secrets/mariadb-password` — nothing
to provision. After rollout, Prometheus target `espocrm` appears under
Status → Targets and the "EspoCRM — Vitals" dashboard auto-loads in Grafana
(ConfigMap label `grafana_dashboard=1`, sidecar pick-up ~30s).
