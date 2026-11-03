#!/usr/bin/env python3
"""wsl_recycle.py — recycle WSL (`wsl --shutdown`) at a safe window.

The shutdown kills this session's whole stack, so it must fire from the
Windows host, not from inside WSL. Two delivery paths, tried in order:

1. Interop live → register a one-shot Scheduled Task on the Windows host
   (fires at --at, default 04:00 local — after the report batch, before
   the morning queue; docker auto-starts the stack on next access).
2. Interop dead (the vsock flakiness that motivated this) → drop
   ``wsl-shutdown.flag`` + the companion ``wsl-recycle-watch.bat`` in the
   Windows home; the existing 1-min flag-watcher task pattern picks it
   up once any trigger file is present, or the bat can be registered as
   a scheduled task manually later.

    wsl_recycle.py [--at 04:00] [--flag-only]

Run from WSL repo root.
"""
import argparse
import os
import subprocess
import sys

WIN_HOME = "/mnt/c/Users/tbaltzakis"
FLAG = os.path.join(WIN_HOME, "wsl-shutdown.flag")
WATCH_BAT = os.path.join(WIN_HOME, "wsl-recycle-watch.bat")

WATCH_BAT_BODY = r"""@echo off
REM Watch for the WSL recycle flag; when present, shut WSL down and
REM delete the flag so it fires exactly once. Register via:
REM   schtasks /create /tn "wsl-recycle-watch" /sc minute /mo 1 /tr "%USERPROFILE%\wsl-recycle-watch.bat"
if exist "%USERPROFILE%\wsl-shutdown.flag" (
    del "%USERPROFILE%\wsl-shutdown.flag"
    wsl.exe --shutdown
)
"""


def interop_alive() -> bool:
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "echo ok"],
            capture_output=True, text=True, timeout=8)
        return "ok" in r.stdout
    except Exception:
        return False


def schedule_windows_task(at: str) -> bool:
    cmd = (
        f"schtasks /create /f /tn \"wsl-recycle\" /sc once /st {at} "
        "/tr \"wsl.exe --shutdown\""
    )
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True, timeout=15)
    ok = "SUCCESS" in r.stdout
    print("schtasks:", r.stdout.strip()[:160] or r.stderr.strip()[:160])
    return ok


def drop_flag() -> None:
    os.makedirs(WIN_HOME, exist_ok=True)
    with open(WATCH_BAT, "w") as f:
        f.write(WATCH_BAT_BODY)
    with open(FLAG, "w") as f:
        f.write("recycle requested\n")
    print(f"flag + watcher written to {WIN_HOME}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="04:00")
    ap.add_argument("--flag-only", action="store_true")
    a = ap.parse_args()

    if not a.flag_only and interop_alive():
        if schedule_windows_task(a.at):
            print(f"one-shot wsl --shutdown scheduled for {a.at} Windows-local")
            return 0
        print("scheduled-task registration failed — falling back to flag")
    else:
        print("interop dead or --flag-only — using flag path")

    drop_flag()
    print(
        "flag queued — it fires when the host watcher task runs it. "
        "To arm the watcher once interop returns:\n"
        "  schtasks /create /tn \"wsl-recycle-watch\" /sc minute /mo 1 "
        f"/tr \"%USERPROFILE%\\wsl-recycle-watch.bat\""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
