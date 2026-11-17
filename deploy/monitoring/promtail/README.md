# promtail — cluster log shipper

Tails every container log under `/var/log/pods` on the `omv` node and pushes
to the Loki gateway (`loki.monitoring.svc:3100`). Closes the gap where only
loki-canary wrote to Loki — completed Job logs now survive cleanup-operator
reaping (needed for xbstream backup/restore forensics).

Deployed 2026-11-17 as a Deployment (single node — DaemonSet would work too
but Deployment keeps the spec identical if more nodes ever join).

## Hard-won details

- `HOSTNAME` env from `spec.nodeName` is **mandatory**. Promtail injects
  `selectors: {field: spec.nodeName=<hostname>}` into kubernetes_sd; without
  the env it uses the pod hostname (the pod name) and discovers **0 targets
  with zero errors**.
- `__path__` glob is `/var/log/pods/*<uid>/<container>/*.log` — pod log dirs
  are named `<ns>_<pod>_<uid>` and log files live one level deeper under the
  container name. A `/*/*.log` glob tails nothing.
- Readiness probes `/ready` return 500 ("unable to find any logs to tail")
  until the first file is tailed — normal for the first ~30s.
- Positions persist on hostPath `/var/lib/promtail` so restarts resume
  without re-shipping history.

## Verify

```bash
kubectl -n monitoring get pod -l app=promtail          # 1/1 Running
kubectl -n monitoring logs -l app=promtail --tail=20   # "Adding target" lines
curl -s http://192.168.1.128:30100/loki/api/v1/label/namespace/values
# expect every workload namespace, not just monitoring/loki-canary
```
