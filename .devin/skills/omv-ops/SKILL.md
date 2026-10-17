---
name: omv-ops
description: >-
  Operate the OMV (OpenMediaVault) NAS host — pending configuration changes,
  mail-alias updates, maintenance/heartbeat report interpretation, and the
  GitHub workflows that drive it via the self-hosted runner. Use when OMV
  reports "pending configuration changes", when a mail alias needs updating,
  or when interpreting the weekly maintenance/heartbeat/nas-auto-remediate
  emails. For Nextcloud on omv see omv-nextcloud-ops; for the mail stack
  see omv-ha-mail.
allowed-tools:
  - read
  - exec
  - grep
  - glob
  - write
  - edit
triggers:
  - user
  - model
---

# OMV Host Ops

OMV runs on `omv-ha` (Pi 4, 192.168.1.130). There is **no direct SSH** from
this machine — all host operations go through GitHub workflows that run on
the self-hosted runner labelled `[self-hosted, omv, pi]`.

## Pending configuration changes

OMV emails "The configuration has been changed. You must apply the changes"
whenever config edits are staged but not deployed.

- Workflow: `cloudless.gr/.github/workflows/omv-apply-pending.yml`
- `apply=false` (default): diagnostics — lists dirty module names from
  `/var/lib/openmediavault/dirtymodules.json` + `Config.isDirty`. No changes.
- `apply=true`: calls `omv-rpc Config applyChanges` — the same RPC as the
  UI's Apply button; deploys all dirty modules AND clears dirtymodules.json.
  Polls `isDirty` up to ~4 min afterwards (deploys are async).
- Never print config values in the workflow — OMV config contains secrets
  (SMTP relay password). Module names only.
- The workflow has a `concurrency` group — safe to dispatch repeatedly.

Dispatch:
```bash
gh workflow run omv-apply-pending.yml -R Themis128/cloudless.gr -f apply=true
```

## Mail aliases

`omv-mail-alias.yml` manages `/etc/aliases` + postfix `recipient_canonical`
+ OMV's `config.xml` notification email. Durable primary address is
`tbaltzakis@cloudless.gr`. Blank-subject mails from the omv host are the
alias workflow's test sends — harmless.

## Reading OMV reports

- **Weekly maintenance**: package upgrades, filesystem checks, SMART, failed
  SSH/systemd units, disk usage, Tailscale. Zero-upgrade + clean SMART is
  normal; root disk ~72% is the standing level.
- **Heartbeat / nas-auto-remediate**: periodic liveness + self-heal logs;
  "no action needed" entries are informational.
- **Pending configuration**: see above — the only item that needs action.
