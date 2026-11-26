#!/bin/sh
# Manual hashed-mount rebind inside the docker-desktop VM (wsl-fsbridge-ops).
set -x
mkdir -p /tmp/ubnt
mountpoint -q /tmp/ubnt || mount /dev/sdd /tmp/ubnt || { for d in sdd sdc sde nvme0n1p1; do mount /dev/$d /tmp/ubnt 2>/dev/null && break; done; }
ls /tmp/ubnt/home/tbaltzakis >/dev/null 2>&1 && echo "UBNT-MOUNT-OK" || echo "UBNT-MOUNT-FAIL"
BASE=/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04
ls "$BASE" | head -40
for src in \
  /home/tbaltzakis/cu130-slim/social-automation/backend/app \
  /home/tbaltzakis/cu130-slim/social-automation/backend/alembic \
  /home/tbaltzakis/cu130-slim/social-automation/backend/scripts \
  /home/tbaltzakis/cu130-slim/social-automation/backend/tests \
  /home/tbaltzakis/cu130-slim/social-automation/backend/dbt \
  /home/tbaltzakis/cu130-slim/social-automation/backend/mlruns \
  /home/tbaltzakis/cu130-slim/social-automation/uploads \
  /home/tbaltzakis/cu130-slim/notebooks \
  /home/tbaltzakis/cu130-slim/.devin \
  /home/tbaltzakis/cu130-slim/matrix/mautrix-meta \
  /home/tbaltzakis/cu130-slim/matrix \
  /home/tbaltzakis/.android \
; do
  h=$(printf '%s' "$src" | sha256sum | awk '{print $1}')
  d="$BASE/$h"
  [ -d "$d" ] || continue
  if [ -z "$(ls -A "$d" 2>/dev/null)" ]; then
    umount -l "$d" 2>/dev/null
    mount --bind "/tmp/ubnt$src" "$d" && echo "REBOUND dir $src -> $h"
  fi
done
for src in /home/tbaltzakis/cu130-slim/.env ; do
  h=$(printf '%s' "$src" | sha256sum | awk '{print $1}')
  d="$BASE/$h"
  [ -e "$d" ] || continue
  if [ -d "$d" ] || [ ! -s "$d" ]; then
    umount -l "$d" 2>/dev/null
    [ -d "$d" ] && rm -rf "$d"
    touch "$d"
    mount --bind "/tmp/ubnt$src" "$d" && echo "REBOUND file $src -> $h"
  fi
done
echo "REBIND-DONE"
