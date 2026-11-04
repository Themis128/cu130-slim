#!/usr/bin/env python3
"""fb_secret_reveal.py — capture the Meta App Secret after the operator types
their FB password into the already-open "re-enter password" dialog in the
debug Chrome (:9222), then write it to .env via env-manager (never printed).

Flow: developers.facebook.com/apps/<APP_ID>/settings/basic/ → "Show" next to
App Secret → Meta password dialog (HUMAN step) → secret revealed in the page →
capture field → PUT /api/env/FACEBOOK_CLIENT_SECRET → verify oauth authorize.

Usage: python fb_secret_reveal.py [--app-id 1936126137016578] [--timeout 600]
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_runner import _ensure_cdp, Page  # noqa: E402

APP_ID = next((a.split("=")[1] for a in sys.argv if a.startswith("--app-id=")),
              "1936126137016578")
TIMEOUT = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--timeout=")), "600"))
ENV_MGR = os.environ.get("ENV_MANAGER_URL", "http://localhost:8199")
REPO_ENV = r"C:\Users\tbaltzakis\cu130-slim\.env"


def _env_val(key):
    with open(REPO_ENV, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def _envmgr_put(key, value):
    user, pw = _env_val("ENV_MANAGER_USER"), _env_val("ENV_MANAGER_PASS")
    req = urllib.request.Request(
        f"{ENV_MGR}/api/env/{key}", method="PUT",
        data=json.dumps({"value": value}).encode(),
        headers={"content-type": "application/json"})
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    r = urllib.request.urlopen(req, timeout=15)
    return r.status


def _dialog_open(p):
    t = p.poll("document.body.innerText", timeout=15) or ""
    return bool(re.search(r"re-enter your password|enter your password", t, re.I))


def _secret_value(p):
    # revealed secret = long hex-ish string in the App Secret row
    return p.poll(
        "(()=>{const t=document.body.innerText;"
        "const i=t.indexOf('App Secret'); if(i<0)return '';"
        "const seg=t.substring(i,i+400);"
        "const m=seg.match(/\\b([0-9a-f]{24,64})\\b/i); return m?m[1]:''})()",
        timeout=15) or ""


def main():
    _ensure_cdp()
    p = Page(url_match="developers.facebook")
    p.goto(f"https://developers.facebook.com/apps/{APP_ID}/settings/basic/")
    time.sleep(5)

    # open the reveal dialog if it isn't already up
    if not _dialog_open(p):
        p.js("(()=>{const b=[...document.querySelectorAll('button,[role=button]')]"
             ".filter(x=>/^Show$/.test((x.innerText||'').trim()));"
             "if(b.length){b[b.length-1].click();}})()")
        time.sleep(3)

    if not _dialog_open(p):
        print("no password dialog — secret may already be visible; probing…")
    else:
        print("password dialog open — waiting for operator to complete it…")

    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        if not _dialog_open(p):
            sec = _secret_value(p)
            if sec:
                rc = _envmgr_put("FACEBOOK_CLIENT_SECRET", sec)
                # verify the OAuth authorize endpoint now produces a URL
                ok = "saved to env-manager rc=%s" % rc
                try:
                    e, pw = _env_val("SOCIAL_ADMIN_EMAIL"), _env_val("SOCIAL_ADMIN_PASSWORD")
                    tok = json.load(urllib.request.urlopen(urllib.request.Request(
                        "http://localhost:8083/api/v1/auth/login",
                        data=f"username={e}&password={pw}".encode(), method="POST"),
                        timeout=15)).get("access_token", "")
                    tid = json.load(urllib.request.urlopen(urllib.request.Request(
                        "http://localhost:8083/api/v1/teams",
                        headers={"Authorization": f"Bearer {tok}"}), timeout=10))[0]["id"]
                    r = urllib.request.urlopen(urllib.request.Request(
                        f"http://localhost:8083/api/v1/auth/oauth/facebook/authorize?team_id={tid}",
                        headers={"Authorization": f"Bearer {tok}"}), timeout=15)
                    print("fb-secret OK —", ok, "| authorize endpoint:", r.status)
                except Exception as ex:
                    print("fb-secret saved —", ok, "| authorize check:", str(ex)[:80])
                return 0
        time.sleep(4)
    print("timeout — password dialog still open; re-run after completing it")
    return 1


if __name__ == "__main__":
    sys.exit(main())
