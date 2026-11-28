#!/usr/bin/env python3
"""adb_rearm.py — recover a dropped Galaxy S22+ ADB link without a manual
settings hunt.

Android 11+ "Wireless debugging" speaks TLS on a ROTATING high port
(observed 42631 on 2026-11-28; legacy `adb tcpip 5555` is a different,
off lane). mDNS discovery does not cross Tailscale, so this tool:

  1. `adb connect` the last-known port (state file / env / :5555 legacy)
  2. parallel TCP scan of the tailnet IP for open ports, then `adb connect`
     each candidate (only paired ports accept; unpaired ones just refuse)
  3. Termux SSH: re-arm adb_wifi + tcp port via settings/setprop
  4. Termux SSH: open the Wireless-debugging settings screen so the user
     only has to flip the toggle
  5. Poll until `adb devices` shows a device (user flips it), then exit.

Usage: adb_rearm.py [--ip 100.84.31.85] [--wait 180]
"""
import argparse
import json
import os
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

SSH = "ssh -p 8022 -o ConnectTimeout=8 -o StrictHostKeyChecking=no u0_a357@{ip}"
STATE_FILE = os.path.expanduser("~/.local/state/phone-adb-port")
SCAN_RANGES = [(30000, 50000), (20000, 30000), (50000, 60000)]
SCAN_THREADS = 400
PROBE_TIMEOUT = 0.6


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def devices():
    out = sh("adb devices").stdout
    return [l.split()[0] for l in out.splitlines()[1:]  # noqa: E741
            if l.strip() and "device" in l and "offline" not in l]


def remember_port(ip: str, port: int) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({"ip": ip, "port": port, "ts": time.time()}, f)
    except OSError:
        pass


def last_known(ip: str) -> list[int]:
    ports = []
    env = os.environ.get("PHONE_ADB_PORT")
    if env and env.isdigit():
        ports.append(int(env))
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        if st.get("ip") == ip and int(st.get("port", 0)) > 0:
            ports.append(int(st["port"]))
    except Exception:
        pass
    ports.append(5555)  # legacy tcpip lane
    return list(dict.fromkeys(ports))


def port_open(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def scan_ports(ip: str) -> list[int]:
    """Parallel TCP-connect scan for open ports in the wireless-debug
    ephemeral range. Cheap on tailnet; only open ports get adb connect."""
    open_ports = []
    for lo, hi in SCAN_RANGES:
        with ThreadPoolExecutor(max_workers=SCAN_THREADS) as ex:
            for port, ok in zip(range(lo, hi + 1),
                                ex.map(lambda p: port_open(ip, p),
                                       range(lo, hi + 1)), strict=True):
                if ok:
                    open_ports.append(port)
        if open_ports:
            break  # first non-empty range is enough
    return open_ports


def try_connect(ip: str, port: int, wait: float = 2.0) -> bool:
    sh(f"adb connect {ip}:{port}")
    time.sleep(wait)
    if devices():
        remember_port(ip, port)
        print(f"connected: {ip}:{port} -> {devices()}")
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="100.84.31.85")
    ap.add_argument("--wait", type=int, default=180)
    ap.add_argument("--scan-only", action="store_true",
                    help="only run the port scan, no termux/settings fallbacks")
    a = ap.parse_args()

    if devices():
        print("adb already up:", devices())
        return

    print("try: last-known ports", last_known(a.ip))
    for port in last_known(a.ip):
        if try_connect(a.ip, port):
            return

    print("try: tailnet port scan (TLS wireless-debugging port rotates)")
    open_ports = scan_ports(a.ip)
    print("open ports:", open_ports or "none")
    for port in open_ports:
        # skip ports already tried and obviously-not-adb (termux ssh 8022)
        if port in last_known(a.ip) or port == 8022:
            continue
        print(f"  adb connect {a.ip}:{port} ...")
        if try_connect(a.ip, port):
            return

    if a.scan_only:
        print("scan found nothing connectable")
        return

    print("try: termux re-arm")
    ssh = SSH.format(ip=a.ip)
    sh(ssh + ' "settings put global adb_wifi_enabled 1;'
             'settings put secure adb_enabled 1;'
             'setprop service.adb.tcp.port 5555"')
    time.sleep(2)
    if devices():
        print("connected:", devices())
        return

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
            # a fresh toggle means a NEW rotating port — rescan for it
            fresh = [p for p in scan_ports(a.ip)
                     if p not in last_known(a.ip) and p != 8022]
            for port in fresh:
                if try_connect(a.ip, port):
                    return
            sh(f"adb connect {a.ip}:5555")
            time.sleep(1)
            print(devices())
            return
    print("timed out waiting — flip the toggle or plug USB")


if __name__ == "__main__":
    main()
