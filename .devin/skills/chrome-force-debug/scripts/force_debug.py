#!/usr/bin/env python3
"""force_debug.py — drive the single-instance-safe CDP bind."""
import json
import subprocess
import sys
import urllib.request
import os

# WSL host IP — the Windows gateway moves subnets on vSwitch rebuilds
# (172.22.144.1 -> 192.168.96.1 observed 2026-11-18). Resolve live.
try:
    WSL_HOST_IP = os.environ.get("WSL_HOST_IP") or subprocess.run(
        ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
        capture_output=True, text=True, timeout=5).stdout.strip() \
        or "172.22.144.1"
except Exception:
    WSL_HOST_IP = "172.22.144.1"



def status():
    for h in ("localhost", "" + WSL_HOST_IP + ""):
        try:
            r = urllib.request.urlopen(f"http://{h}:9222/json/version", timeout=4)
            return {"bound": True, "via": h, "browser": json.loads(r.read()).get("Browser")}
        except Exception:
            pass
    return {"bound": False}


def kill():
    subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                    "Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue"],
                   capture_output=True, timeout=20)
    return {"killed": True}


def flag():
    import os
    d = "/mnt/c/Users/tbaltzakis/cu130-slim/.devin/triggers"
    os.makedirs(d, exist_ok=True)
    open(f"{d}/force-debug.flag", "w").close()
    return {"flag": "dropped", "watcher_bat": "chrome-force-debug.bat"}


if __name__ == "__main__":
    print(json.dumps(globals()[sys.argv[1]]() if len(sys.argv) > 1 else status(), indent=1))
