#!/usr/bin/env python3
"""adb_watch.py — reconnect to the S22+ and act the moment adbd returns."""
import argparse
import subprocess
import sys
import time

HOST, PORT = "100.84.31.85", 5555
ADB = "/tmp/platform-tools/adb"


def sh(*a, t=10):
    try:
        return subprocess.run(a, capture_output=True, text=True,
                              timeout=t).stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def device_up():
    out = sh(ADB, "-s", target, "shell", "echo", "1", t=8)
    return out == "1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", help="on-device .sh to run when device is up")
    ap.add_argument("--push", help="local file to push to /sdcard/ first")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--host", default=HOST, help="phone IP (default Tailscale)")
    ap.add_argument("--poll", type=int, default=15)
    a = ap.parse_args()
    global target
    target = f"{a.host}:{PORT}"
    end = time.time() + a.timeout
    while time.time() < end:
        r = sh(ADB, "connect", target)
        if device_up():
            print(f"device up ({r})")
            if a.push:
                out = sh(ADB, "-s", target, "push", a.push,
                         f"/sdcard/{a.push.rsplit('/', 1)[-1]}", t=30)
                print("pushed:", out[:120])
            if a.script:
                out = sh(ADB, "-s", target, "shell",
                         "sh", a.script, t=180)
                print(out[-3000:])
            return 0
        print(f"waiting… ({r[:60]})")
        time.sleep(a.poll)
    print("timeout — still offline")
    return 1


if __name__ == "__main__":
    sys.exit(main())
