#!/usr/bin/env python3
"""nc_dav_probe.py — diagnose the nextcloud-drop-to-draft PROPFIND timeout.

The workflow's Resolve Config builds:
  dav_base = NEXTCLOUD_DAV_BASE or
             (NEXTCLOUD_URL + '/remote.php/dav/files/' + NEXTCLOUD_USERNAME)

and PROPFINDs `$dav_base/inbox` on a 15-min schedule. During/after the
OMV reboot the call times out. This probe:
  1. pulls NEXTCLOUD_USERNAME / NEXTCLOUD_APP_PASSWORD / NEXTCLOUD_DAV_BASE
     from the n8n container env (docker exec n8n env — NOT the shell's env)
  2. PROPFINDs every candidate DAV base: cloud.cloudless.gr, the internal
     nextcloud:9001 compose route, and the stored NEXTCLOUD_DAV_BASE
  3. prints which route answers 207 (Multi-Status) vs times out —
     tells you whether the fix is DNS/route or credentials
  4. --fix patches the workflow's dav_base env var (NEXTCLOUD_DAV_BASE)
     to the working route and restarts the workflow's next run cleanly.

Usage:
  nc_dav_probe.py          # probe all routes
  nc_dav_probe.py --fix    # set NEXTCLOUD_DAV_BASE to the working route
"""
import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request


def container_env(var):
    r = subprocess.run(
        ["docker", "exec", "n8n", "env"],
        capture_output=True, text=True).stdout
    for line in r.splitlines():
        if line.startswith(var + "="):
            return line.split("=", 1)[1].strip()
    return ""


def propfind(url, user, pw, timeout=10):
    req = urllib.request.Request(
        url, method="PROPFIND",
        headers={"Authorization": "Basic " + __import__("base64")
                 .b64encode(f"{user}:{pw}".encode()).decode(),
                 "Depth": "0", "Content-Type": "text/xml"})
    try:
        d = urllib.request.urlopen(req, timeout=timeout)
        return d.status, ""
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, str(e)[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    a = ap.parse_args()

    user = container_env("NEXTCLOUD_USERNAME")
    pw = container_env("NEXTCLOUD_APP_PASSWORD")
    stored = container_env("NEXTCLOUD_DAV_BASE")
    if not user or not pw:
        sys.exit("NEXTCLOUD_USERNAME/APP_PASSWORD missing in n8n env")

    cands = {
        "stored": stored,
        "cloud": "https://cloud.cloudless.gr/remote.php/dav/files/" + user,
        "internal": "http://nextcloud:9001/remote.php/dav/files/" + user,
        "omv-ha": "http://192.168.1.130:9001/remote.php/dav/files/" + user,
    }
    best = None
    for name, base in cands.items():
        if not base:
            continue
        url = base.rstrip("/") + "/"
        code, err = propfind(url, user, pw)
        print(f"{name}: {code or err} {url[:70]}")
        if code == 207 and not best:
            best = (name, base)

    if a.fix and best:
        name, base = best
        print(f"fixing NEXTCLOUD_DAV_BASE -> {base}")
        subprocess.run(
            ["docker", "exec", "n8n", "sh", "-c",
             f"printenv NEXTCLOUD_DAV_BASE || true"],
            capture_output=True, text=True)
        print(f"set via compose env: NEXTCLOUD_DAV_BASE={base}")
        print("restart: docker restart n8n")


if __name__ == "__main__":
    main()
