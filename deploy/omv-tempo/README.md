# omv Tempo — Cloudflare Traces + origin OTel

Self-hosted trace backend for the `cloudless.gr` zone. Lives on omv
(`192.168.1.200`), SSD-backed, 7-day retention matching the Cloudflare
free-tier window.

## Pipeline

```
Cloudflare zone tracing (10% baseline sampling, inbound traceparent
rejected, context forwarded to origin)
        │  OTLP/HTTP
        ▼
otel.cloudless.gr  ──Cloudflare Tunnel──▶  tempo-proxy :4319
        (nginx, X-OTLP-Token auth — 403 without token)
        ▼
tempo :4318 (OTLP HTTP ingest)  ·  :3200 (query API)  ·  :4317 (gRPC)
        ▼
Grafana (grafana.cloudless.gr, Tempo datasource auto-provisioned)
```

social-api also exports FastAPI/httpx spans directly to
`192.168.1.200:4318` when `OTEL_ENABLED=true`. Edge and origin spans are
stored under different trace IDs (Cloudflare beta gap) — both halves are
queryable, not joined.

## Layout

| File | Purpose |
|---|---|
| `tempo.yml` | Tempo single-binary config — local backend on the SSD, `trace` retention 7d |
| `docker-compose.yml` | `tempo` + `tempo-proxy` (token auth) |
| `tempo-proxy.conf.example` | nginx template for `otel.cloudless.gr` — the OTLP token lives in `~/.cache/cf-ops/otlp-token.json` on the ops workstation, never in the repo |

## Data that flows out

Tempo's 7-day retention means edge signal would otherwise expire —
`export_datalake` (every 6h, `social-worker-default`) queries the `:3200`
search API with TraceQL and writes
`lake/socialauto-edge-metrics/daily.json` to the R2 datalake: per-hostname
sampled trace count, p50/p95/max latency, `errors_4xx`/`errors_5xx`.

TraceQL gotchas encoded there:

- `\.` escapes are rejected in regex strings — use bare dots
- `http.response.status_code` is indexed **numerically** — `= 500`, never `= "500"`
- `url.full` and `status_code` live on different spans — combine them with
  a structural `&&` spanset join: `{span.url.full =~ "…"} && {span.http.response.status_code >= 500}`

## Verify

```
curl -s http://192.168.1.200:3200/api/search -G \
  --data-urlencode 'q={span.url.full =~ "https://cloud.cloudless.gr/.*"}' \
  --data-urlencode start=$(date -d '-1 hour' +%s) --data-urlencode end=$(date +%s)
```

`deploy/monitoring/cloudflare/README.md` covers the complementary
metrics path (GraphQL → Prometheus exporter).
