#!/usr/bin/env python3
"""phone_adb.py — one entrypoint for Galaxy S22+ ADB over both lanes.

Lanes (auto-picked, --lane overrides):
  usb   — USB-C cable. WSL2 cannot see USB devices, so this lane shells
          out to the Windows platform-tools adb.exe (auto-detected).
  net   — wireless ADB over Tailscale: 100.84.31.85:5555 (LAN 192.168.1.77).
          Re-enable from USB with: phone_adb.py tcpip

Examples:
  phone_adb.py devices
  phone_adb.py shell getprop ro.product.model
  phone_adb.py shell dumpsys notification --noredact
  phone_adb.py pull /sdcard/file ./file
  phone_adb.py tcpip            # re-enable wireless ADB via USB lane
  phone_adb.py --lane net shell wm size
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

NET_TARGET = os.environ.get("PHONE_ADB_NET", "100.84.31.85:5555")
LAN_TARGET = os.environ.get("PHONE_ADB_LAN", "192.168.1.77:5555")
WIN_ADB_CANDIDATES = [
    os.environ.get("PHONE_ADB_EXE", ""),
    "/mnt/c/Users/tbaltzakis/Android/Sdk/platform-tools/adb.exe",
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
]


def adb_bin() -> str:
    for c in WIN_ADB_CANDIDATES:
        if c and Path(c).exists():
            return c
    return "adb"


def run(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def serials() -> list[str]:
    """Live device serials from whichever adb server we drive."""
    out = run([adb_bin(), "devices"]).stdout
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]


def pick_lane(prefer: str | None) -> tuple[str, str]:
    """Return (lane, serial). USB preferred unless --lane net."""
    ss = serials()
    usb = next((s for s in ss if ":" not in s), None)
    net = next((s for s in ss if s == NET_TARGET or s == LAN_TARGET), None)
    if prefer == "usb" and usb:
        return "usb", usb
    if prefer == "net" and net:
        return "net", net
    if prefer == "usb" and not usb:
        sys.exit("usb lane: no device — plug in USB-C (Windows adb.exe sees it)")
    if prefer == "net" and not net:
        sys.exit(f"net lane: {NET_TARGET} not connected — run `phone_adb.py tcpip` over USB first")
    if usb:
        return "usb", usb
    if net:
        return "net", net
    # nothing live — try connecting tailnet once, then re-check
    run([adb_bin(), "connect", NET_TARGET], timeout=20)
    ss = serials()
    net = next((s for s in ss if ":" in s), None)
    if net:
        return "net", net
    sys.exit("no phone reachable — plug USB-C or enable wireless ADB on tailnet")


def cmd_tcpip(args):
    "`adb tcpip 5555` must run over USB; then connect over tailnet."""
    lane, serial = pick_lane("usb")
    print(f"[usb] {serial}: switching adbd to tcpip 5555")
    r = run([adb_bin(), "-s", serial, "tcpip", "5555"], timeout=20)
    print(r.stdout.strip() or r.stderr.strip())
    import time
    time.sleep(2)
    for target in (NET_TARGET, LAN_TARGET):
        r = run([adb_bin(), "connect", target], timeout=20)
        print(r.stdout.strip())
        if "connected" in r.stdout:
            print(f"[net] {target} live")
            return
    sys.exit("tcpip done but tailnet connect failed — check Tailscale on the phone")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lane", choices=["usb", "net"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices")
    sub.add_parser("tcpip")
    sp = sub.add_parser("shell"); sp.add_argument("args", nargs=argparse.REMAINDER)
    fp = sub.add_parser("forward"); fp.add_argument("args", nargs=argparse.REMAINDER)
    pp = sub.add_parser("pull"); pp.add_argument("args", nargs=argparse.REMAINDER)
    ps = sub.add_parser("push"); ps.add_argument("args", nargs=argparse.REMAINDER)
    args = ap.parse_args()

    if args.cmd == "devices":
        print(run([adb_bin(), "devices", "-l"]).stdout)
        return
    if args.cmd == "tcpip":
        cmd_tcpip(args)
        return

    lane, serial = pick_lane(args.lane)
    print(f"[adb:{lane}] {serial}", file=sys.stderr)
    subcmd = {"shell": ["shell"], "forward": ["forward"],
              "pull": ["pull"], "push": ["push"]}[args.cmd]
    r = run([adb_bin(), "-s", serial, *subcmd, *args.args], timeout=120)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
