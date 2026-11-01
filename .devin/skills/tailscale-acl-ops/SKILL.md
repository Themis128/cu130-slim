---
name: tailscale-acl-ops
description: Tailscale SSH ACL enable + verify for omv-ha. The tailnet ACL
  needs one `ssh` block (admin console, no API token in .env — one-time
  manual). Tool: scripts/ts_acl_check.py — `check` probes tailnet+ssh
  end-to-end via omv-main relay, `auto` polls until the ACL lands then
  runs omv_ha_migrate (bootstrap-key → migrate → drop-dead).
---
# tailscale-acl-ops
One ACL `ssh` block unlocks the whole omv-ha offload — the tool verifies it.
