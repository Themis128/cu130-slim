#!/usr/bin/env python3
"""viber_provision — wire a Viber bot token into SocialAuto.

    viber_provision.py --token <viber auth token> [--name cloudless]
    viber_provision.py --verify            # viber_tool info + webhook check

The token comes from partners.viber.com (create/select bot → App Token).
Everything after the paste is automated: connect → validate → webhook set.
"""
import argparse
import json
import os
import sys
import subprocess
import urllib.parse
import urllib.request

API = os.environ.get("SOCIAL_API", "http://127.0.0.1:8083")
TEAM = os.environ.get("SOCIAL_TEAM_ID", "10024d25-2ea4-40ea-86c6-ba89f515a2eb")


def _env():
    for p in ("/mnt/c/Users/tbaltzakis/cu130-slim/.env",
              "/home/tbaltzakis/cu130-slim/.env"):
        if os.path.exists(p):
            return dict(l.split("=", 1) for l in open(p) if "=" in l and not l.startswith("#"))
    return {}


def _login():
    env = {k.strip(): v.strip().strip('"') for k, v in _env().items()}
    r = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=urllib.parse.urlencode({"username": env["SOCIAL_ADMIN_EMAIL"],
                                     "password": env["SOCIAL_ADMIN_PASSWORD"]}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(urllib.request.urlopen(r, timeout=20).read()).get("access_token")


def connect(token, name):
    r = urllib.request.Request(
        f"{API}/api/v1/viber/connect",
        data=json.dumps({"auth_token": token, "set_webhook": True}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_login()}"}, method="POST")
    try:
        d = json.loads(urllib.request.urlopen(r, timeout=30).read())
        print("wired:", json.dumps(d)[:200])
    except urllib.error.HTTPError as e:
        print("connect err:", e.code, e.read()[:250])


def verify():
    r = subprocess.run(
        ["docker", "exec", "social-api", "python", "/app/scripts/viber_tool.py", "accounts"],
        capture_output=True, text=True, timeout=30)
    print(r.stdout.strip() or r.stderr.strip()[:200])
    r2 = subprocess.run(
        ["docker", "exec", "social-api", "python", "/app/scripts/viber_tool.py", "webhook"],
        capture_output=True, text=True, timeout=30)
    print(r2.stdout.strip()[:300] or r2.stderr.strip()[:200])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token"); ap.add_argument("--name", default="cloudless")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify: verify()
    elif a.token: connect(a.token, a.name)
    else: ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
