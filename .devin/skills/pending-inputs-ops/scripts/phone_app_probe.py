#!/usr/bin/env python3
"""phone_app_probe.py — server-side evidence of which container-apps the
owner's phone is connected to. Answers "do I have Nextcloud/Grafana on my
phone" without ADB: reads the servers' session/device lists for the phone's
client signatures.

    phone_app_probe.py      # check every reachable surface
"""
import sys
import urllib.request

SURFACES = [
    ("nextcloud", "https://cloud.cloudless.gr/ocs/v2.php/apps/provisioning_api/users", 
     "dav|android|ios|mobile"),
    ("grafana", "https://grafana.cloudless.gr/api/user/auth-tokens",
     "android|ios|mobile"),
    ("appflowy", "https://appflowy.cloudless.gr/api/user/sessions",
     "android|ios|mobile"),
]


def probe(name: str, url: str, pattern: str) -> int:
    try:
        r = urllib.request.urlopen(url, timeout=8)
        body = r.read().decode("utf-8", "replace").lower()
        hits = [p for p in pattern.split("|") if p in body]
        print(f"{name:12} {'phone-connected: '+','.join(hits) if hits else 'no phone sessions'} ({len(body)}b)")
        return 0
    except Exception as e:
        print(f"{name:12} unreachable — {str(e)[:50]}")
        return 1


if __name__ == "__main__":
    sys.exit(all(probe(*s) for s in SURFACES) and 0 or 1)
