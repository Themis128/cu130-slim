#!/usr/bin/env python3
"""dmr-tunnel-watch — keep omv:12436 -> this PC's Docker Model Runner alive.

The reverse tunnel is launched by the Startup-folder VBS
(dmr-tunnel-hidden.vbs -> conhost --headless dmr-tunnel.bat, which loops
`ssh -N -R 0.0.0.0:12436:127.0.0.1:12436 github-omv`). When Windows
reboots or the ssh child dies, nothing restarts it — host_gates_check
only *reports* dmr_tunnel degraded. This watcher closes the loop:

  1. Probe omv:12436 listener over ssh (same check as host_gates).
  2. If absent, relaunch the Startup VBS via wscript (hidden, no popup).
  3. Deduped TG #ops alert on state transitions only.

Cron: every 10 min. Safe to run anytime — no-op while tunnel is up.
"""
import json
import os
import subprocess
import sys
import time

STATE = os.path.expanduser("~/.local/state/dmr-tunnel-watch.json")
VBS = (r"C:\Users\tbaltzakis\AppData\Roaming\Microsoft\Windows\Start Menu"
       r"\Programs\Startup\dmr-tunnel-hidden.vbs")
TG = os.path.expanduser(
    "~/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py")


def omv_listening() -> bool:
    try:
        out = subprocess.run(
            ["ssh", "-i", os.path.expanduser("~/.ssh/omv_id_rsa"),
             "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             "omv", "ss -tln | grep -c ':12436 '"],
            capture_output=True, text=True, timeout=25).stdout.strip()
        return out.isdigit() and int(out) > 0
    except Exception:
        return False  # can't tell -> don't thrash


def relaunch() -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f'wscript.exe "{VBS}"'],
        capture_output=True, timeout=30)


def tg(text: str) -> None:
    try:
        subprocess.run(["python3", TG, "ops", f"dmr-tunnel-watch: {text}"],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def main() -> int:
    up = omv_listening()
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    was_up = st.get("up")

    if up:
        if was_up is False:
            tg("omv:12436 listening again — tunnel healed")
        st["up"] = True
        st["last_ok"] = int(time.time())
    else:
        if was_up is not False:
            tg("omv:12436 NOT listening — relaunching dmr-tunnel VBS")
        relaunch()
        st["up"] = False
        st["last_fail"] = int(time.time())

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"))
    print(f"dmr-tunnel-watch: omv_12436={'up' if up else 'down(relaunching)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
