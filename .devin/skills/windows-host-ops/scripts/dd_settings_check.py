#!/usr/bin/env python3
"""dd_settings_check.py — Docker Desktop settings drift watchdog status.

Read-only probe of the DD-Settings-Watchdog lane (DevOptimizer):
  - settings-store.json drift state (IntegratedWslDistros / default WSL)
  - watchdog state file (last check/drift/restore/restart, episode flag)
  - last lines of the watchdog log

The 2026-11-13 incident: the old script read the deleted settings.json,
false-positived drift every 5 min, and restarted Docker Desktop every pass
(vsock flap loop). The rewritten script only restarts when the settings
file was actually rewritten, max once per 6h.

Usage:
    python3 dd_settings_check.py            # status table
    python3 dd_settings_check.py --json     # machine-readable
    python3 dd_settings_check.py --log N    # last N log lines (default 8)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APPDATA_DOCKER = Path("/mnt/c/Users/tbaltzakis/AppData/Roaming/Docker/settings-store.json")
LEGACY = Path("/mnt/c/Users/tbaltzakis/AppData/Roaming/Docker/settings.json")
LOG = Path("/mnt/d/DevOptimizer/logs/dd-settings-watchdog.log")
STATE = Path("/mnt/d/DevOptimizer/logs/dd-settings-watchdog-state.json")

DESIRED_DISTRO = "Ubuntu-24.04"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def collect(log_lines: int = 8) -> dict:
    out: dict = {"ok": True, "problems": []}

    store_exists = APPDATA_DOCKER.exists()
    legacy_exists = LEGACY.exists()
    out["settings_file"] = (
        "settings-store.json" if store_exists else
        "settings.json (legacy)" if legacy_exists else "MISSING"
    )
    if not store_exists and not legacy_exists:
        out["ok"] = False
        out["problems"].append("no Docker Desktop settings file found")

    drift = []
    if store_exists:
        s = _read_json(APPDATA_DOCKER)
        distros = s.get("IntegratedWslDistros") or []
        default_wsl = s.get("EnableIntegrationWithDefaultWslDistro")
        out["IntegratedWslDistros"] = distros
        out["EnableIntegrationWithDefaultWslDistro"] = default_wsl
        if DESIRED_DISTRO not in distros:
            drift.append(f"IntegratedWslDistros missing {DESIRED_DISTRO}: {distros}")
        if default_wsl is not True:
            drift.append(f"EnableIntegrationWithDefaultWslDistro={default_wsl} (want true)")
    out["drift"] = drift
    if drift:
        out["ok"] = False
        out["problems"].extend(drift)

    state = _read_json(STATE) if STATE.exists() else {}
    out["watchdog_state"] = {
        "lastCheck": state.get("lastCheck"),
        "lastDrift": state.get("lastDrift"),
        "lastRestore": state.get("lastRestore"),
        "lastRestart": state.get("lastRestart"),
        "driftEpisode": state.get("driftEpisode"),
        "driftCount": state.get("driftCount"),
        "restoreCount": state.get("restoreCount"),
    }
    if not state:
        out["problems"].append("watchdog state file missing - task may never have run")
        out["ok"] = False

    # staleness: lastCheck older than 15 min means the 5-min task is dead
    last_check = state.get("lastCheck")
    if last_check:
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(last_check)
            if ts.tzinfo is None:
                ts = ts.astimezone()
            age_min = (datetime.now(ts.tzinfo) - ts).total_seconds() / 60
            out["watchdog_state"]["age_minutes"] = round(age_min, 1)
            if age_min > 15:
                out["ok"] = False
                out["problems"].append(f"watchdog lastCheck {age_min:.0f}min ago - task stale")
        except Exception:
            pass

    if LOG.exists():
        lines = LOG.read_text(errors="replace").splitlines()
        out["log_tail"] = lines[-log_lines:]
    else:
        out["log_tail"] = []
        out["problems"].append("watchdog log missing")
        out["ok"] = False

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", type=int, default=8, metavar="N")
    args = ap.parse_args()

    data = collect(args.log)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0 if data["ok"] else 1

    print(f"settings file : {data['settings_file']}")
    print(f"distros       : {data.get('IntegratedWslDistros')}")
    print(f"default WSL   : {data.get('EnableIntegrationWithDefaultWslDistro')}")
    st = data["watchdog_state"]
    print(f"watchdog      : lastCheck={st.get('lastCheck')} "
          f"age={st.get('age_minutes', '?')}min driftEpisode={st.get('driftEpisode')} "
          f"drifts={st.get('driftCount')} restores={st.get('restoreCount')}")
    if st.get("lastRestart"):
        print(f"last DD restart: {st['lastRestart']} (6h cooldown)")
    if data["log_tail"]:
        print("--- log tail ---")
        for ln in data["log_tail"]:
            print(f"  {ln}")
    if data["problems"]:
        print("PROBLEMS:")
        for p in data["problems"]:
            print(f"  ! {p}")
    else:
        print("OK - no drift, watchdog fresh")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
