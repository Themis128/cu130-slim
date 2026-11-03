#!/usr/bin/env python3
"""force_debug.py — drive the single-instance-safe CDP bind."""
import json
import subprocess
import sys
import urllib.request


def status():
    for h in ("localhost", "172.22.144.1"):
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
