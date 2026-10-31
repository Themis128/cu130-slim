---
name: dmr-gateway-ops
description: The Model Serving adopt for the DMR — a named-endpoint gateway
  in front of :12435. Routes logical endpoints (content-gen, embed, eval)
  to backend models with traffic-percent splits, health-aware fallback
  (skips models dmr_doctor marks hung), and per-caller usage logging.
  Tool: scripts/dmr_gateway.py — `serve` runs it on :12436, `status` maps
  routes, `config` edits the endpoint table.
---
# DMR gateway — named endpoints + traffic routing + usage tracking
