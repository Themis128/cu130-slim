#!/usr/bin/env python3
"""meta_system_user.py — mint a NEVER-expiring Meta System User token via API.

Automates the whole chain (research-verified):
  POST /{business_id}/system_users               → create system user
  POST /{system_user_id}/access_tokens           → permanent token
    with business_app=<APP_ID>, scope=<scopes>, appsecret_proof

Prereq: an admin USER access token with `business_management` scope —
mint one via SocialAuto OAuth (authorize URL emits client_id now) or
Graph API Explorer. The user token is the ONLY input; everything after
is pure API.

Usage:
  python meta_system_user.py --admin-token <EAA...> [--business-id ID]
      [--name socialauto-daemon] [--role EMPLOYEE]
      [--scopes pages_manage_posts,pages_show_list,pages_read_engagement,business_management]
Stores the minted token via cred_inject vault + env-manager (never printed).
"""
import hashlib
import hmac
import json
import sys
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"
APP_ID = "1936126137016578"


def call(method, path, params):
    data = None
    if method == "POST":
        data = urllib.parse.urlencode(params).encode()
        params = {}
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(f"{GRAPH}/{path}{qs}", data=data, method=method)
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        print("API error:", e.read().decode()[:300])
        sys.exit(1)


def main():
    tok = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--admin-token=")), "")
    bid = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--business-id=")), "")
    name = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--name=")), "socialauto-daemon")
    role = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--role=")), "EMPLOYEE")
    scopes = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--scopes=")),
                  "pages_manage_posts,pages_show_list,pages_read_engagement,instagram_basic,instagram_content_publish,business_management")
    if not tok:
        sys.exit("need --admin-token (mint via SocialAuto OAuth or Graph Explorer w/ business_management)")

    # app secret from .env
    secret = ""
    for line in open(r"C:\Users\tbaltzakis\cu130-slim\.env", encoding="utf-8", errors="replace"):
        if line.startswith("FACEBOOK_CLIENT_SECRET="):
            secret = line.split("=", 1)[1].strip().strip('"')
    if not secret:
        sys.exit("FACEBOOK_CLIENT_SECRET missing in .env")

    # business id — auto-discover if not given
    if not bid:
        r = call("GET", "me/businesses", {"access_token": tok})
        data = r.get("data", [])
        if not data:
            sys.exit("no business portfolio found on this token — supply --business-id")
        bid = data[0]["id"]
        print(f"business: {data[0].get('name', bid)} ({bid})")

    # create (or reuse) system user
    su = call("POST", f"{bid}/system_users",
              {"name": name, "role": role, "access_token": tok})
    suid = su.get("id")
    if not suid:
        # may already exist → list and match
        r = call("GET", f"{bid}/system_users", {"access_token": tok})
        suid = next((u["id"] for u in r.get("data", []) if u["name"] == name), None)
        if not suid:
            sys.exit("could not create/find system user — " + json.dumps(su)[:200])
    print(f"system user id: {suid}")

    # mint permanent token
    proof = hmac.new(secret.encode(), tok.encode(), hashlib.sha256).hexdigest()
    r = call("POST", f"{suid}/access_tokens", {
        "business_app": APP_ID, "scope": scopes,
        "access_token": tok, "appsecret_proof": proof})
    sys_token = r.get("access_token")
    if not sys_token:
        sys.exit("mint failed — " + json.dumps(r)[:200])

    # store via vault + env-manager
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    try:
        import cred_inject
        cred_inject.cred_set("cloudless-gate:fb-system-user", "meta-system-user", sys_token)
        print("stored in vault")
        print("env rc:", cred_inject.env_write("FACEBOOK_SYSTEM_USER_TOKEN", sys_token))
    except Exception as e:
        print("store failed:", str(e)[:120])
    print("permanent system-user token minted — assign assets in Business Settings if #200 on calls")


if __name__ == "__main__":
    main()
