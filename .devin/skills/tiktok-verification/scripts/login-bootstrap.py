#!/usr/bin/env python3
"""Bootstrap a TikTok session in the engagement-browser sidecar.

Opens the TikTok login in the noVNC browser, polls until a session
cookie lands (user logs in manually in the VNC window), then confirms.
Usage: python login-bootstrap.py [--wait-minutes 10]
"""
import json
import subprocess
import sys
import time

S = "http://localhost:9223"

def post(p, b):
    r = subprocess.run(["docker","exec","engagement-browser","curl","-s","-X","POST",
        S+p,"-H","Content-Type: application/json","-d",json.dumps(b)],
        capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return {}

def get(p):
    r = subprocess.run(["docker","exec","engagement-browser","curl","-s",S+p],
                       capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return {}

def js(e): return post("/session/evaluate", {"expression": e})

# 1 — open the login page
post("/session/navigate", {"url": "https://www.tiktok.com/login"})
time.sleep(6)
print("opened tiktok login — sign in inside http://localhost:6082/vnc.html")

# 2 — poll for session cookies (sessionid / sid_tt / uid_tt)
deadline = time.time() + int(sys.argv[sys.argv.index("--wait-minutes")+1] if "--wait-minutes" in sys.argv else "10")*60
while time.time() < deadline:
    c = get("/session/cookies")
    names = c.get("cookies_found") or []
    if any(n in names for n in ("sessionid","sid_tt","uid_tt")):
        print("session captured:", names)
        break
    # also check if the page moved off the login screen
    url = js("location.href").get("result","")
    if url and "/login" not in url and "tiktok.com" in url:
        print("left login page:", url)
        time.sleep(3)
    time.sleep(5)
else:
    print("timeout — no session yet"); sys.exit(1)

# 3 — extract + persist
post("/session/extract", {"platform":"tiktok"})
print("extracted — session is persisted. Run resubmit-via-sidecar.py next.")
