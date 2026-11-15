#!/usr/bin/env python3
import json
import os
import urllib.parse
import urllib.request

API = os.environ.get("SOCIAL_API_INTERNAL", "http://localhost:8000")
TEAM = "10024d25-2ea4-40ea-86c6-ba89f515a2eb"


def req(path, token, method="GET"):
    r = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, method=method)
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


tok = req("/api/v1/auth/login", "", method="POST") if False else None
# login via form post
r = urllib.request.Request(
    f"{API}/api/v1/auth/login",
    data=urllib.parse.urlencode({"username": os.environ["SOCIAL_ADMIN_EMAIL"], "password": os.environ["SOCIAL_ADMIN_PASSWORD"]}).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
tok = json.loads(urllib.request.urlopen(r, timeout=30).read())["access_token"]

for p in ["instagram-onboarding", "threads"]:
    try:
        res = req(f"/api/v1/auth/oauth/{p}/authorize?team_id={TEAM}", tok)
        print(f"== {p} ==")
        print(res.get("authorization_url") or json.dumps(res)[:400])
    except Exception as e:
        print(f"== {p} == ERROR {e}")
