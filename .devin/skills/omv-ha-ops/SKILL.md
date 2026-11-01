---
name: omv-ha-ops
description: Offload light services from omv-main (Pi 5, 8GB, RAM-starved)
  to omv-ha (Pi 4, 1GB, mail host) over Tailscale. Moves searxng + tempo +
  drops dead cloudflared-omv tunnel. `tailscale ssh` preferred over keys —
  Tailscale SSH needs no local keys. Tool: scripts/omv_ha_migrate.py —
  `status` (memory + service audit both nodes), `migrate` (export → scp →
  redeploy), `drop-dead` (remove the dead omv-cloudless tunnel container).
---
# omv-ha-ops — tailscale service offload
Pi 4 frees ~600MB-1GB on omv-main to stop the OOM cycling.
