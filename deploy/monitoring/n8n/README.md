# n8n metrics target

Scrapes `n8n_*` series from the `n8n-metrics` compose container
(`192.168.1.23:9392/metrics`, nginx proxy that exposes ONLY `/metrics` from
the loopback-bound n8n — see `docker-compose.yml` and
`deploy/nginx/n8n-metrics.conf`).

n8n 2.43+ with `N8N_METRICS=true` exports default process metrics plus
(scheduler, workflow-id label, API endpoints enabled in compose):

- `n8n_scheduler_*` — durable-scheduler queue depth, lag, dispatch, dead-letters
- `n8n_executions_*` / `n8n_workflow_*` — execution counters with `workflow_id` label
- `n8n_http_request_*` — public API endpoint metrics

Apply on omv: `./apply.sh` then check target `n8n` in Prometheus
`/api/v1/targets`. If `up{job="n8n"}=0` while the container is healthy:
Docker Desktop restart broke the Windows port-proxy — `docker restart n8n-metrics`.
