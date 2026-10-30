#!/usr/bin/env python3
"""triggers.py — one command for every remaining human trigger.
Checks each external gate + prints the exact click/action to fire it."""
import urllib.request
import subprocess

def cdp():
    try:
        urllib.request.urlopen("http://localhost:9222/json/version", timeout=3)
        return "BOUND — cdp_watch is driving"
    except Exception:
        return "unbound → run C:\\Users\\tbaltzakis\\chrome-debug-launch.bat"

def bluesky():
    r = subprocess.run(["docker","compose","exec","-T","social-api",
                        "python","scripts/bluesky_tool.py","accounts"],
                       capture_output=True,text=True,timeout=25)
    return "active" if "active" in r.stdout else "needs .env app-password"

def viber():
    r = subprocess.run(["docker","compose","exec","-T","social-api",
                        "python","scripts/viber_tool.py","accounts"],
                       capture_output=True,text=True,timeout=25)
    return "connected" if r.stdout.strip() and "no viber" not in r.stdout else "needs app token → viber_provision --token"

for n,f in [("cdp-9222",cdp),("bluesky",bluesky),("viber",viber)]:
    print(f"{n}: {f()}")
