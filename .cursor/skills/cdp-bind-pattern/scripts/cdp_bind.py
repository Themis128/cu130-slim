#!/usr/bin/env python3
"""cdp_bind.py — one command for the whole :9222 chain.
status -> bound/unbound + which pieces are live; arm -> install the watcher
Run-key + drop the flag; probe -> the full diagnosis (chrome, relay, port)."""
import json
import os
import subprocess
import sys
import urllib.request

FLAG = "/mnt/c/Users/tbaltzakis/cu130-slim/.devin/triggers/launch-chrome.flag"


def _sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def status():
    gw = []
    for l in open("/proc/net/route").read().splitlines()[1:]:
        f = l.split()
        if len(f) > 2 and f[1] == "00000000":
            h = f[2]
            gw.append(".".join(str(int(h[i:i+2], 16)) for i in (6, 4, 2, 0)))
    gw = gw or ["172.22.144.1"]
    out = {"gw": gw[0]}
    for h in ("localhost", gw[0]):
        try:
            r = urllib.request.urlopen(f"http://{h}:9222/json/version", timeout=4)
            out["bound"] = True
            out["via"] = h
            out["browser"] = json.loads(r.read()).get("Browser")
            return out
        except Exception:
            pass
    out["bound"] = False
    out["chrome_running"] = os.path.exists(
        "/mnt/c/Users/tbaltzakis/chrome-debug-profile/SingletonLock")
    out["flag"] = "pending" if os.path.exists(FLAG) else "consumed/absent"
    return out


def arm():
    os.makedirs(os.path.dirname(FLAG), exist_ok=True)
    open(FLAG, "w").close()
    return {"flag": "dropped", "watcher": "chrome-debug-watch.ps1 -> cdp-bind-user.bat"}


def probe():
    s = status()
    s["chrome_proc"] = bool(_sh(["powershell.exe", "-NoProfile", "-Command",
                                "(Get-Process chrome -EA SilentlyContinue).Count"]).stdout.strip())
    return s


if __name__ == "__main__":
    print(json.dumps(globals()[sys.argv[1]]() if len(sys.argv) > 1 else status(), indent=1))
