---
name: dmr-gateway-ops
description: The Model Serving adopt for the DMR — a named-endpoint gateway
  in front of :12435. Routes logical endpoints (content-gen, embed, eval)
  to backend models with traffic-percent splits, health-aware fallback
  (quarantines a model for UNHEALTHY_TTL_S on connect errors/timeouts/5xx,
  then re-probes it via dmr_doctor before it serves again — client 4xx
  never counts as a model failure), and per-caller usage logging.
  Tool: scripts/dmr_gateway.py — `serve` runs it on :12440, `status`
  queries the running gateway's GET /v1/status (falls back to the static
  route table). Edit the ENDPOINTS table at the top of the script to
  change routes/splits.
---
# DMR gateway — named endpoints + traffic routing + usage tracking
