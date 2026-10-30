#!/usr/bin/env python3
"""adb_rearm.py — recover a dropped Galaxy S22+ ADB link without a manual
settings hunt.

Try order:
  1. `adb connect <ip>:5555` (wireless debug session just needs reconnect)
  2. Termux SSH: re-arm adb_wifi + tcp port via settings/setprop
  3. Termux SSH: open the Wireless-debugging settings screen on the phone
     so the user only has to flip the toggle
  4. Poll until `adb devices` shows a device (user flips it), then exit.

Usage: adb_rearm.py [--ip 192.168.1.77] [--wait 180]
"""
import argparse
import subprocess
import sys
import time

SSH = "ssh -p 8022 -o ConnectTimeout=8 -o StrictHostKeyChecking=no u0_a357@{ip}"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def devices():
    out = sh("adb devices").stdout
    return [l.split()[0] for l in out.splitlines()[1:]
            if l.strip() and "device" in l and "offline" not in l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="192.168.1.77")
    ap.add_argument("--wait", type=int, default=180)
    a = ap.parse_args()

    if devices():
        print("adb already up:", devices()); return

    print("try: wireless reconnect")
    sh(f"adb connect {a.ip}:5555")
    time.sleep(2)
    if devices():
        print("connected:", devices()); return

    print("try: termux re-arm")
    ssh = SSH.format(ip=a.ip)
    sh(ssh + ' "settings put global adb_wifi_enabled 1;'
             'settings put secure adb_enabled 1;'
             'setprop service.adb.tcp.port 5555"')
    time.sleep(2)
    if devices():
        print("connected:", devices()); return

    print("try: open wireless-debugging screen on the phone")
    sh(ssh + ' "am start -a android.settings.ADB_WIRELESS_SETTINGS ||'
             'am start -a android.settings.DEVELOPMENT_SETTINGS"')
    print("wireless-debugging screen is up on the phone — toggle it ON")
    deadline = time.time() + a.wait
    while time.time() < deadline:
        time.sleep(5)
        d = devices()
        if d:
            print("adb back:", d)
            sh(f"adb connect {a.ip}:5555")
            time.sleep(1)
            print(devices())
            return
    print("timed out waiting — flip the toggle or plug USB")


if __name__ == "__main__":
    main()
