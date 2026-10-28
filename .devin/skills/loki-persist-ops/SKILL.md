---
name: loki-persist-ops
description: >
  Upgrade the omv k3s Loki from test-schema+filesystem to R2/S3-backed
  storage — real schema_config (tsdb/v13, 24h index period) + chunks on
  the datalake R2 bucket. Fixes the no-persistence caveat of the
  single-binary test install. Tool: scripts/loki_persist.sh.
---

# Loki Persist Ops — R2-backed storage

The initial `loki` helm release uses `useTestSchema` + `filesystem` —
no retention guarantee. `loki_persist.sh` upgrades it to S3/R2 chunks
(same bucket as the datalake) + a real schema.

Needs env: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`LOKI_BUCKET` (default `datalake-bucket`).

After upgrade: logs persist across pod restarts, query history survives,
Grafana datasource works the same (`loki.monitoring.svc:3100`).

## Status (2026-10-28)

Persistence upgrade ATTEMPTED — hit two issues over ssh heredoc:
- `export ENDPOINT='$ENDPOINT'` exported the literal, not the value
- `loki-memberlist` svc missing → "too many unhealthy instances in the ring"

**Correct path**: write a `loki-values.yaml` on the omv node with the
full S3 config + memberlist settings, then
`helm upgrade loki grafana/loki -n monitoring -f loki-values.yaml`.
The test-schema install is live + digest keeps working until then.
