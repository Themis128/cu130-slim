# SocialAuto monitoring

Prometheus scrape wiring + Grafana dashboards for the SocialAuto stack.

`social-api` exposes `/metrics` (loopback only); the compose `social-metrics`
nginx container proxies just that path onto the LAN so the omv Prometheus can
scrape it. The headless `socialauto-metrics` Service + static `Endpoints` in
this directory point kube-prom at that LAN address — **if the host IP changes,
update the Endpoints IP in `service.yaml` and re-run `./apply.sh`**.

## Panels

- `SocialAuto — Vitals` (existing `grafana-dashboard-socialauto-vitals`)
- `SocialAuto — Social Platforms` (`dashboard-social-platforms.json`): per-
  platform view of every connected social app — connected accounts, publish
  queue by platform+status, published/failed targets in the last 7d, latest
  follower snapshots, and OAuth token expiry countdown per account.

The per-platform gauges (`socialauto_publish_queue_platform`,
`socialauto_posts_published_7d`, `socialauto_publish_failures_7d`,
`socialauto_followers_latest`, `socialauto_token_expires_in_seconds`) are
produced by `social-automation/backend/app/services/metrics.py` and appear
after the social-api image carrying them is deployed.

## Deploy (on omv, via Tailscale)

```sh
cd deploy/monitoring/socialauto
./apply.sh
```
