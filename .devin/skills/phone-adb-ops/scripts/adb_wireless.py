#!/usr/bin/env python3
"""adb_wireless — Android 11+ wireless-debugging (TLS/mDNS) lane.

The legacy `adb tcpip 5555` lane (phone_adb.py) dies when the user toggles
Developer-options "Wireless debugging" — adbd then only speaks TLS on a
rotating mDNS port. This tool covers that lane:

  discover — `adb mdns services` → live _adb-tls-connect / _adb-tls-pairing
             endpoints (IP:port; the connect port CHANGES every toggle)
  pair     — `adb pair IP:PORT CODE` (6-digit code from the phone's
             "Pair device with pairing code" dialog; --auto scrapes it via
             uiautomator when a dump is reachable)
  connect  — `adb connect IP:PORT` to the discovered connect port
  status   — which lane is live right now (usb / tcpip-5555 / tls-mdns)

Usage:
    adb_wireless.py status
    adb_wireless.py discover
    adb_wireless.py connect            # auto: discover → connect
    adb_wireless.py pair --code 123456 # or --auto to read the dialog
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

PAIR_RE = re.compile(r"_adb-tls-pairing\._tcp\s+(\S+?)\s*$", re.M)
CONN_RE = re.compile(r"_adb-tls-connect\._tcp\s+(\S+?)\s*$", re.M)
IPPORT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]+):(\d+)")
PAIR_CODE_RE = re.compile(r"pairing code[^\d]{0,40}(\d{6})", re.I)


def adb_bin() -> str:
    for c in ("adb", "adb.exe"):
        try:
            subprocess.run([c, "version"], capture_output=True, timeout=10, check=True)
            return c
        except Exception:
            continue
    sys.exit("adb not found on PATH")


def run(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run([adb_bin(), *args], capture_output=True, text=True, timeout=timeout)


def mdns() -> str:
    r = run(["mdns", "services"], timeout=15)
    return r.stdout + r.stderr


def endpoints(kind: str) -> list[str]:
    """Return IP:port list for pairing or connect services."""
    text = mdns()
    pat = PAIR_RE if kind == "pair" else CONN_RE
    out = []
    for m in pat.finditer(text):
        tail = m.group(1)
        ep = IPPORT_RE.search(tail)
        if ep:
            out.append(f"{ep.group(1)}:{ep.group(2)}")
    return sorted(set(out))


def status() -> None:
    r = run(["devices", "-l"], timeout=15)
    live = [l for l in r.stdout.splitlines() if re.search(r"\sdevice\s", l)]
    print(r.stdout.strip() or "(no devices)")
    pairs, conns = endpoints("pair"), endpoints("connect")
    print(f"mdns pairing endpoints: {pairs or 'none'}")
    print(f"mdns connect endpoints:  {conns or 'none'}")
    if any(":5555" in l for l in live):
        print("lane: tcpip-5555 (legacy)")
    elif live:
        print("lane: tls-mdns wireless debugging")
    elif pairs or conns:
        print("lane: wireless debugging ON but not connected — run `connect`")
    else:
        print("lane: none (enable Wireless debugging in Developer options)")


def discover() -> None:
    pairs, conns = endpoints("pair"), endpoints("connect")
    print(f"pairing: {pairs or 'none'}")
    print(f"connect: {conns or 'none'}")
    if not pairs and not conns:
        sys.exit("no mDNS endpoints — is Wireless debugging ON and same subnet?")


def connect() -> None:
    conns = endpoints("connect")
    if not conns:
        sys.exit("no _adb-tls-connect endpoint — enable Wireless debugging on the phone")
    for ep in conns:
        r = run(["connect", ep], timeout=20)
        print(f"connect {ep}: {r.stdout.strip() or r.stderr.strip()}")
        if "connected" in (r.stdout + r.stderr):
            time.sleep(1)
            status()
            return
    sys.exit("connect failed on all endpoints — try `pair` first if never paired")


def scrape_pair_code() -> str | None:
    """Best-effort: uiautomator dump via an existing session to read the
    pairing dialog. Returns None when no session/screen access."""
    devs = run(["devices"], timeout=10).stdout
    serial = next((l.split("\t")[0] for l in devs.splitlines() if "\tdevice" in l), None)
    if not serial:
        return None
    run(["-s", serial, "shell", "uiautomator", "dump", "/sdcard/_pair.xml"], timeout=20)
    xml = run(["-s", serial, "shell", "cat", "/sdcard/_pair.xml"], timeout=15).stdout
    m = PAIR_CODE_RE.search(re.sub(r"<[^>]+>", " ", xml))
    return m.group(1) if m else None


def pair(code: str | None, auto: bool) -> None:
    pairs = endpoints("pair")
    if not pairs:
        sys.exit("no _adb-tls-pairing endpoint — open 'Pair device with pairing code' on the phone")
    if auto and not code:
        code = scrape_pair_code()
        if not code:
            sys.exit("--auto could not read the dialog (needs an existing adb session); pass --code")
    if not code or not re.fullmatch(r"\d{6}", code):
        sys.exit("need a 6-digit pairing code (--code NNNNNN or --auto)")
    for ep in pairs:
        r = run(["pair", ep, code], timeout=30)
        print(f"pair {ep}: {r.stdout.strip() or r.stderr.strip()}")
        if "paired" in (r.stdout + r.stderr).lower() and "failed" not in (r.stdout + r.stderr).lower():
            connect()
            return
    sys.exit("pairing failed on all endpoints")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "discover", "connect", "pair"])
    ap.add_argument("--code", help="6-digit wireless-debugging pairing code")
    ap.add_argument("--auto", action="store_true", help="scrape the pairing dialog via uiautomator")
    args = ap.parse_args()
    {"status": status, "discover": discover, "connect": connect,
     "pair": lambda: pair(args.code, args.auto)}[args.cmd]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
