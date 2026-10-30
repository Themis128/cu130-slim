#!/usr/bin/env python3
"""phone_tune.py — apply the S22+ operational profile in one shot.

Locks portrait (coordinate-tap safety), doze-whitelists the ops apps so
Termux-SSH/Nextcloud/Slack/ntfy survive Doze, and optionally restores
notification volumes (we muted them during the alert storm).

Usage:
  phone_tune.py            # apply profile (rotation lock + whitelist)
  phone_tune.py --unmute   # restore notification + ring volumes (11)
  phone_tune.py --rotate 1 # restore auto-rotation
"""
import argparse
import subprocess

DEV = "RFCTA0AEKZP"
DOZE_APPS = [
    "com.nextcloud.client", "com.termux", "com.Slack", "io.heckel.ntfy",
    "com.whatsapp", "com.facebook.katana", "com.nextcloud.talk2",
    "com.google.android.gm", "com.github.android",
    "com.samsung.android.app.clockpackage",
]


def sh(*a):
    return subprocess.run(["adb", "-s", DEV, "shell", *a],
                          capture_output=True, text=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unmute", action="store_true")
    ap.add_argument("--rotate", type=int, choices=(0, 1))
    a = ap.parse_args()

    if a.rotate is not None:
        sh("settings", "put", "system", "accelerometer_rotation",
           str(a.rotate))
        print("auto-rotation:", a.rotate)
    else:
        sh("settings", "put", "system", "accelerometer_rotation", "0")
        sh("settings", "put", "system", "user_rotation", "0")
        print("rotation locked (portrait)")

    if a.unmute:
        sh("settings", "put", "system", "volume_notification", "11")
        sh("settings", "put", "system", "volume_ring", "11")
        print("volumes restored to 11")

    for p in DOZE_APPS:
        sh("dumpsys", "deviceidle", "whitelist", "+" + p)
    print(f"doze whitelist applied to {len(DOZE_APPS)} apps")


if __name__ == "__main__":
    main()
