#!/usr/bin/env python3
"""adb_watch.py — reconnect to the S22+ and act the moment adbd returns."""
import argparse, subprocess, sys, time

HOST, PORT = "100.84.31.85", 5555
ADB = "/tmp/platform-tools/adb"


def sh(*a, t=10):
    return subprocess.run(a, capture_output=True, text=True,
                          timeout=t).stdout.strip()


def device_up():
    out = sh(ADB, "-s", f"{HOST}:{PORT}", "shell", "echo", "1", t=8)
    return out == "1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", help="on-device .sh to run when device is up")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--poll", type=int, default=15)
    a = ap.parse_args()
    end = time.time() + a.timeout
    while time.time() < end:
        r = sh(ADB, "connect", f"{HOST}:{PORT}")
        if device_up():
            print(f"device up ({r})")
            if a.script:
                out = sh(ADB, "-s", f"{HOST}:{PORT}", "shell",
                         "sh", a.script, t=180)
                print(out[-3000:])
            return 0
        print(f"waiting… ({r[:60]})")
        time.sleep(a.poll)
    print("timeout — still offline")
    return 1


if __name__ == "__main__":
    sys.exit(main())
