#!/usr/bin/env python3
"""chrome_launch.py — bind :9222 through every available WSL→Windows path.

Order: status → try each launcher → file-drop trigger (the Windows watcher
finishes it)."""
import json
import os
import subprocess
import sys
import time
import urllib.request

FLAG = "/mnt/c/Users/tbaltzakis/cu130-slim/.devin/triggers/launch-chrome.flag"
BAT = r"C:\\Users\\tbaltzakis\\chrome-debug-launch.bat"
PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
CMD = "/mnt/c/Windows/System32/cmd.exe"
SCHTASKS = "/mnt/c/Windows/System32/schtasks.exe"
EXPLORER = "/mnt/c/Windows/explorer.exe"


def bound() -> bool:
    try:
        urllib.request.urlopen("http://localhost:9222/json/version", timeout=4)
        return True
    except Exception:
        return False


def try_cmd(args, timeout=15):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return "ERROR" not in r.stdout + r.stderr
    except Exception:
        return False


def fire():
    if bound():
        print("already bound"); return 0
    paths = [
        [SCHTASKS, "/run", "/tn", "CloudlessDebugChrome"],
        [EXPLORER, BAT],
        [PS, "-Command", f"Start-Process '{BAT}'"],
        [CMD, "/c", BAT],
    ]
    for p in paths:
        try_cmd(p)
        time.sleep(6)
        if bound():
            print(f"bound via {p[0]}"); return 0
    # fallback — the file-drop trigger for the Windows watcher
    os.makedirs(os.path.dirname(FLAG), exist_ok=True)
    open(FLAG, "w").write(str(int(time.time())))
    print(f"launch flag dropped → {FLAG}")
    return 2


def status():
    print(json.dumps({"cdp": "bound" if bound() else "unbound"}, indent=2))


def arm_watcher():
    """Write the Windows-side watcher .bat + register a 1-min task (needs one
    successful interop call to install)."""
    watcher = r"""@echo off
if exist "C:\Users\tbaltzakis\cu130-slim\.devin\triggers\launch-chrome.flag" (
  del /q "C:\Users\tbaltzakis\cu130-slim\.devin\triggers\launch-chrome.flag"
  call C:\Users\tbaltzakis\chrome-debug-launch.bat
)"""
    wp = "/mnt/c/Users/tbaltzakis/chrome-launch-watch.bat"
    open(wp, "w").write(watcher)
    ok = try_cmd([SCHTASKS, "/create", "/tn", "CloudlessChromeWatch",
                  "/tr", "C:\\Users\\tbaltzakis\\chrome-launch-watch.bat",
                  "/sc", "minute", "/mo", "1", "/f"])
    print("watcher installed" if ok else "install failed — run the .bat registration manually")


if __name__ == "__main__":
    sys.exit({"fire": fire, "status": status, "arm-watcher": arm_watcher}[
                 sys.argv[1] if len(sys.argv) > 1 else "status"]())
