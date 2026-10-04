# Cloudflare monitoring — omv Grafana

One-screen Grafana dashboard for every Cloudflare service the cloudless.gr
account uses: edge traffic, tunnels, Workers, R2.

## Architecture

```
Cloudflare GraphQL Analytics API
        │  (read-only token, 5-min polls)
        ▼
cloudflare-exporter  (python:3.12-slim + collector.py, ns=monitoring)
        │  /metrics :8080
        ▼
ServiceMonitor → Prometheus (kube-prom) → Grafana dashboard
                                        uid: cloudflare-overview
cloudflared native /metrics (.128/.130:20241) ── scraped separately,
                                        powers the Tunnels row
```

## Why a custom collector

`lablabs/cloudflare_exporter` builds its zone query around
`httpRequests1mGroups` — a paid-plan dataset. On Free zones the entire zone
query fails with `authz` and **no zone metrics are emitted at all**
(Workers/R2 account metrics work fine). `collector.py` only queries
Free-plan datasets:

| Dataset | Scope | Metrics |
|---|---|---|
| `httpRequestsAdaptiveGroups` | zone | requests by host/status/cache/country, origin p50/95/99 ms |
| `httpRequests1dGroups` | zone | daily requests/bytes/threats/pageviews |
| `workersInvocationsAdaptive` | account | per-script requests/errors/duration, CPU quantiles |
| `r2OperationsAdaptiveGroups` | account | per-bucket API ops |
| `r2StorageAdaptiveGroups` | account | objects + payload bytes (36h window — sampled hourly) |
| `firewallEventsAdaptiveGroups` | zone | optional — skipped silently on Free |

Not available on Free (by design, not a bug): per-minute edge TTFB,
firewall-event detail, `httpRequests1mGroups`.

## API token

Read-only account token `cf-grafana-analytics` (id `51038970cbf3e798...`),
created via `scripts/cf_tokens.py` machinery with:

- `Analytics Read` (zone `7025298073d6a5c645a6ad9add0cbf0e`)
- `Account Analytics Read` + `Account Settings Read` (account)
- `Zone Read` + `Zone Observability Read` (zone list + observability datasets)

Secret (never committed): `kubectl create secret generic cloudflare-exporter
-n monitoring --from-literal=token=<value>`

## Deploy

```sh
ssh tbaltzakis@192.168.1.200   # omv node, k3s
cd deploy/monitoring/cloudflare && sudo ./apply.sh
```

## Dashboard

`dashboard-cloudflare-overview.json` — pushed via the Grafana API, live at
`grafana.tail4ecae1.ts.net/d/cloudflare-overview`. Every panel carries a
description explaining what it measures and how to interpret it.

## Cloudflare features evaluated (2026-10 observability launch)

- **Unified analytics** — used; this collector is exactly that, surfaced in Grafana.
- **Custom dashboards (CF-native)** — available, but we keep one Grafana pane.
- **Alert webhooks** — free on all plans; natural next step (edge 5xx / tunnel
  down → Alertmanager webhook → Slack digest).
- **Logpush on Free** — now free; candidate to push HTTP logs to
  `datalake-bucket` (R2) later.
- **Unified SQL API** — beta; could replace collector.py later, not needed now.
- **Observability MCP** — possible future MCP server for ad-hoc queries.
- **OHTTP Gateway** — closed beta, paid add-on. No anonymous-client privacy
  use case in the stack today; documented as future option only. OHTTP is
  also unobservable by design — there'd be nothing to chart.
