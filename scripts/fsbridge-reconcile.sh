#!/usr/bin/env bash
# fsbridge-reconcile.sh — retire the fsbridge volume-transplant overrides in
# docker-compose.override.yml once the Docker Desktop WSL fsbridge heals
# (wsl --shutdown / dd-user-distro-proxy respawn).
#
# Context: 2026-11-15 flap #4 — poisoned \\wsl.localhost hashed mounts forced
# named-volume transplants for cloudflared/api-gateway/social-metrics/
# n8n-metrics/promtail/stack-watchers. This script detects the heal, strips
# those override blocks, recreates the services on their real bind mounts,
# removes the scratch volumes, and alerts ops. Idempotent + throttled — safe
# to call from the 5-min social-stack-watchdog.
#
# State: ~/.local/state/fsbridge-reconcile/last-probe (epoch, throttle 30min)
# Log:   ~/logs/fsbridge-reconcile.log
set -u

REPO="${FSBR_REPO:-/home/tbaltzakis/cu130-slim}"
OVERRIDE="$REPO/docker-compose.override.yml"
DOCTOR="$REPO/.devin/skills/wsl-fsbridge-ops/scripts/fsbridge_doctor.py"
STATE_DIR="${FSBR_STATE_DIR:-/home/tbaltzakis/.local/state/fsbridge-reconcile}"
LOG_DIR="${FSBR_LOG_DIR:-/home/tbaltzakis/logs}"
LOG="$LOG_DIR/fsbridge-reconcile.log"
TG_ALERT="${FSBR_TG_ALERT:-/home/tbaltzakis/cu130-slim/scripts/tg_alert.py}"
PROBE_EVERY="${FSBR_PROBE_EVERY:-1800}"   # seconds between mount probes
MARKER="fsbridge workaround"

# services + volumes that exist in the override ONLY for the transplant
TRANSPLANT_SERVICES="cloudflared api-gateway social-metrics n8n-metrics promtail stack-watchers"
TRANSPLANT_VOLUMES="cf-entrypoint agw-conf agw-upstream metrics-conf n8n-conf promtail-cfg promtail-data-vol"

mkdir -p "$STATE_DIR" "$LOG_DIR"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$LOG"; }

# --- cheap gates (run every watchdog pass, must stay fast) ---
[ -f "$DOCTOR" ] || { log "doctor missing: $DOCTOR"; exit 0; }
command -v docker >/dev/null 2>&1 || exit 0

# Work items: transplant-override retirement and/or stack-ops-bridge
# retirement. Either one keeps the script alive; neither → exit.
NEEDED=0
if [ -f "$OVERRIDE" ] && grep -q "$MARKER" "$OVERRIDE"; then NEEDED=1; fi
docker inspect stack-ops-bridge >/dev/null 2>&1 && NEEDED=1
[ "$NEEDED" = 1 ] || exit 0

# --- throttle the expensive mount probe ---
now=$(date -u +%s)
last=$(cat "$STATE_DIR/last-probe" 2>/dev/null || echo 0)
[ "$((now - last))" -lt "$PROBE_EVERY" ] && exit 0
echo "$now" >"$STATE_DIR/last-probe"

# --- definitive probe: fresh \\wsl.localhost mount must be readable ---
out=$(python3 "$DOCTOR" probe 2>&1) || true
echo "$out" | tail -3 >>"$LOG"
if ! echo "$out" | grep -q "mount OK"; then
  log "fsbridge still poisoned — transplants stay"
  exit 0
fi
log "fsbridge HEALED — retiring interim workarounds"

# --- deploy lock for the compose ops below ---
echo "$(date -u +%FT%TZ) devin fsbridge-reconcile: heal detected" >"$REPO/.deploy.lock"
trap 'rm -f "$REPO/.deploy.lock"' EXIT

# --- retire stack-ops-bridge interim proxy ---
# The bridge holds the same host ports real stack-ops publishes, so it must
# be removed BEFORE `compose up -d stack-ops` can bind them. If the recreate
# fails, bring the bridge back — routes stay up either way.
BRIDGE_SH="$REPO/scripts/stack-ops-bridge.sh"
if docker inspect stack-ops-bridge >/dev/null 2>&1; then
  log "retiring stack-ops-bridge interim proxy"
  docker rm -f stack-ops-bridge >>"$LOG" 2>&1 || true
  if (cd "$REPO" && timeout 900 docker compose up -d stack-ops >>"$LOG" 2>&1); then
    sleep 10
    st=$(docker inspect -f '{{.State.Status}}' stack-ops 2>/dev/null || echo missing)
    if [ "$st" = "running" ]; then
      log "stack-ops restored on real mounts — bridge retired"
      /usr/bin/python3 "$TG_ALERT" ops "fsbridge healed — stack-ops-bridge interim proxy retired, real stack-ops back (wake/sleep + :8787 restored)" >>"$LOG" 2>&1 || true
    else
      log "stack-ops recreate failed ($st) — restoring bridge"
      bash "$BRIDGE_SH" up >>"$LOG" 2>&1 || true
      /usr/bin/python3 "$TG_ALERT" ops "fsbridge healed but stack-ops recreate failed ($st) — bridge restored, manual check needed" >>"$LOG" 2>&1 || true
    fi
  else
    log "stack-ops compose up FAILED — restoring bridge"
    bash "$BRIDGE_SH" up >>"$LOG" 2>&1 || true
    /usr/bin/python3 "$TG_ALERT" ops "fsbridge healed but stack-ops recreate FAILED — bridge restored, manual check needed" >>"$LOG" 2>&1 || true
  fi
fi

# --- strip the transplant blocks, keep everything else (e.g. infisical) ---
if [ -f "$OVERRIDE" ] && grep -q "$MARKER" "$OVERRIDE"; then
cp "$OVERRIDE" "$OVERRIDE.bak-$(date -u +%Y%m%dT%H%M%SZ)"
python3 - "$OVERRIDE" $TRANSPLANT_SERVICES $TRANSPLANT_VOLUMES <<'PY'
import sys, re

path, services, volumes = sys.argv[1], set(sys.argv[2:-7]), set(sys.argv[-7:])
lines = open(path).read().splitlines(keepends=True)

# locate top-level "services:" and "volumes:" blocks
def block_bounds(lines, key):
    start = end = None
    for i, l in enumerate(lines):
        if re.match(rf"^{key}:\s*$", l):
            start = i
            continue
        if start is not None and re.match(r"^\S", l):
            end = i
            break
    return (start, end if end is not None else len(lines))

svc_start, svc_end = block_bounds(lines, "services")
vol_start, vol_end = block_bounds(lines, "volumes")

out = lines[:]

def drop_service_blocks(seg):
    """Remove '  <name>:' blocks (2-space indent) for transplant services."""
    res, i = [], 0
    while i < len(seg):
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", seg[i])
        if m and m.group(1) in services:
            i += 1
            while i < len(seg) and (seg[i].startswith("    ") or seg[i].strip() == ""):
                i += 1
            continue
        res.append(seg[i]); i += 1
    return res

def drop_volume_entries(seg):
    """Remove '  <name>:' entries inside a volumes: block."""
    res, i = [], 0
    while i < len(seg):
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", seg[i])
        if m and m.group(1) in volumes:
            i += 1
            while i < len(seg) and (seg[i].startswith("    ") or seg[i].strip() == ""):
                i += 1
            continue
        res.append(seg[i]); i += 1
    return res

if svc_start is not None:
    out[svc_start:svc_end] = drop_service_blocks(lines[svc_start:svc_end])
if vol_start is not None:
    # recompute after service edit — volumes block is after services
    vol_start, vol_end = block_bounds(out, "volumes")
    if vol_start is not None:
        newvol = drop_volume_entries(out[vol_start:vol_end])
        # if nothing but the 'volumes:' header remains, drop the section
        if not any(l.strip() and not l.startswith("volumes:") for l in newvol[1:]):
            del out[vol_start:vol_end]
        else:
            out[vol_start:vol_start + len(out[vol_start:vol_end])] = newvol

open(path, "w").writelines(out)
PY

# --- recreate the six services on their real bind mounts ---
if ! (cd "$REPO" && timeout 900 docker compose up -d $TRANSPLANT_SERVICES >>"$LOG" 2>&1); then
  log "compose up FAILED — restoring override backup"
  cp "$OVERRIDE" "$OVERRIDE.stripped-$(date -u +%Y%m%dT%H%M%SZ)"
  latest_bak=$(ls -t "$OVERRIDE".bak-* 2>/dev/null | head -1)
  [ -n "$latest_bak" ] && cp "$latest_bak" "$OVERRIDE"
  (cd "$REPO" && timeout 900 docker compose up -d $TRANSPLANT_SERVICES >>"$LOG" 2>&1) || true
  /usr/bin/python3 "$TG_ALERT" ops "fsbridge healed but transplant retirement FAILED — override restored, manual check needed" >>"$LOG" 2>&1 || true
  exit 1
fi

# --- verify all six came up ---
sleep 20
bad=""
for c in social-cloudflared social-api-gateway social-metrics n8n-metrics promtail stack-watchers; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)
  [ "$st" = "running" ] || bad="$bad $c=$st"
done
if [ -n "$bad" ]; then
  log "post-recreate not running:$bad — restoring override"
  latest_bak=$(ls -t "$OVERRIDE".bak-* 2>/dev/null | head -1)
  [ -n "$latest_bak" ] && cp "$latest_bak" "$OVERRIDE"
  (cd "$REPO" && timeout 900 docker compose up -d $TRANSPLANT_SERVICES >>"$LOG" 2>&1) || true
  /usr/bin/python3 "$TG_ALERT" ops "fsbridge reconcile: containers unhealthy after revert ($bad) — override restored" >>"$LOG" 2>&1 || true
  exit 1
fi

# --- drop the scratch volumes ---
docker volume rm $TRANSPLANT_VOLUMES >>"$LOG" 2>&1 || true

log "transplants retired — all six services on native bind mounts"
/usr/bin/python3 "$TG_ALERT" ops "fsbridge healed (wsl --shutdown / proxy respawn) — volume transplants auto-retired, all six services recreated on native mounts" >>"$LOG" 2>&1 || true
else
  log "no transplant overrides pending — nothing to strip"
fi
exit 0
