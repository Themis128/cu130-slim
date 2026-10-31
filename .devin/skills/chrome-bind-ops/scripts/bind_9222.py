#!/usr/bin/env python3
"""bind_9222.py — every remaining path to bind :9222 in the interactive session.
schedule: a one-time schtasks /IT task (runs in the logged-on session, not a
background service). install-runkey: a registry Run-key entry that fires the
watcher at next logon. status: probe :9222 on localhost + the Windows host IP."""
import json
import subprocess
import sys
import time
import urllib.request

BAT = r"C:\Users\tbaltzakis\chrome-debug-launch.bat"
PS1 = r"C:\Users\tbaltzakis\chrome-debug-watch.ps1"


def _sh(cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _ps(ps):
    return _sh(["powershell.exe", "-NoProfile", "-Command", ps])


def status():
    for host in ("localhost",):
        try:
            r = urllib.request.urlopen(f"http://{host}:9222/json/version", timeout=4)
            return {"bound": True, "browser": json.loads(r.read()).get("Browser")}
        except Exception:
            pass
    import socket
    gw = []
    for l in open("/proc/net/route").read().splitlines()[1:]:
        f = l.split()
        if len(f) > 2 and f[1] == "00000000":
            h = f[2]
            gw.append(".".join(str(int(h[i:i+2], 16)) for i in (6, 4, 2, 0)))
    gw = gw or ["172.22.144.1"]
    try:
        r = urllib.request.urlopen(f"http://{gw[0]}:9222/json/version", timeout=4)
        return {"bound": True, "browser": json.loads(r.read()).get("Browser"), "via": gw[0]}
    except Exception:
        return {"bound": False}


def schedule():
    # One-time task in 60s, /IT -> runs in the interactive session
    at = time.strftime("%H:%M", time.localtime(time.time() + 60))
    r = _ps(f'schtasks /create /tn ChromeDebugBind /sc once /st {at} /it '
            f'/tr "{BAT}" /f')
    return {"sched": at, "out": r.stdout.strip() + r.stderr.strip()[:200]}


def install_runkey():
    r = _ps('New-ItemProperty -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" '
            f'-Name ChromeDebugWatch -PropertyType String -Value '
            f'"powershell -WindowStyle Hidden -File {PS1}" -Force')
    return {"runkey": "installed", "out": r.returncode}


def install_watcher():
    r = _ps(f'powershell -WindowStyle Hidden -Command "Start-Process powershell -ArgumentList \'-File {PS1}\' -WindowStyle Hidden"')
    return {"watcher": "started", "out": r.returncode}


if __name__ == "__main__":
    print(json.dumps(globals()[sys.argv[1]]() if len(sys.argv) > 1 else status(), indent=1))
