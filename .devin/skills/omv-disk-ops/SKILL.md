# omv-disk-ops

Manage the omv k3s SSD (`sda1`, 119GB) disk pressure — the hourly
`[OMV] WARNING: Disk NN%` mails come from the monitoring/disk-watchdog
CronJob watching sda1, which is **not** the root filesystem.

## The mismatch that matters

`nas-auto-remediate` only watches `/` (74% — fine). The 82% warnings are
sda1 = containerd images + etcd + local-path PVs. Self-heals via
`pi-disk-cleanup` timer (03:00 daily) + remediate at 85%, but the
hourly warnings fire at ~80%.

## Tool

```bash
python3 .devin/skills/omv-disk-ops/scripts/omv_disk.py status   # latest % from alert mail
python3 .devin/skills/omv-disk-ops/scripts/omv_disk.py run      # trigger omv-disk-prune.yml
python3 .devin/skills/omv-disk-ops/scripts/omv_disk.py watch    # poll run → df delta
```

## omv-disk-prune.yml (cloudless.gr)

`workflow_dispatch` on the `[self-hosted, omv]` runner: `crictl rmi --prune`,
`journalctl --vacuum-size`, optional `docker system prune -af`, df before/after.
Only visible to dispatch **after merge to main**.

## If the prune doesn't help

sda1 growth = image layers or a PV ballooning — `du -sh /var/lib/rancher/k3s/*`
on omv identifies which. `local-path` PVs live under
`/srv/dev-disk-by-uuid-a9a5a108-…` — don't move the nodePath.
