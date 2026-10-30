#!/usr/bin/env python3
"""bsky_provision — provision a Bluesky account + app password + wire it.

Three credential paths, in order of preference:

    bsky_provision.py from-password --handle cloudless-gr.bsky.social \\
        --password <main pw>
        # createSession → createAppPassword → POST /connect. Zero human steps
        # once you have the main password.

    bsky_provision.py create --handle cloudless-gr.bsky.social \\
        --email tbaltzakis@cloudless.gr
        # → requests phone verification, prints what happened. bsky.social
        # requires SMS on new accounts; when the SMS code arrives:
    bsky_provision.py create --handle ... --email ... \\
        --phone +30XXXXXXXXXX --code 123456
        # → finishes createAccount → app password → /connect.

    bsky_provision.py connect --handle <h> --app-password <p>
        # wire an already-known app password into social_accounts.

Run host-side
calls the social-api /api/v1/bluesky/connect endpoint via
admin creds from repo .env (SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD).
"""
import argparse
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request

PDS = "https://bsky.social"
API = os.environ.get("SOCIAL_API", "http://127.0.0.1:8083")
TEAM = os.environ.get("SOCIAL_TEAM_ID", "10024d25-2ea4-40ea-86c6-ba89f515a2eb")


def _env() -> dict:
    env = {}
    for p in ("/mnt/c/Users/tbaltzakis/cu130-slim/.env",
              "/home/tbaltzakis/cu130-slim/.env"):
        if os.path.exists(p):
            for l in open(p):
                if "=" in l and not l.startswith("#"):
                    k, v = l.split("=", 1)
                    env[k.strip()] = v.strip().strip('"')
            break
    return env


def _post(url, body, headers=None, timeout=25):
    r = urllib.request.Request(url, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json",
                                        **(headers or {})}, method="POST")
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"raw": "http error"}


def _xrpc(method, body, jwt=None):
    return _post(f"{PDS}/xrpc/{method}", body,
                 {"Authorization": f"Bearer {jwt}"} if jwt else None)


def _login() -> str:
    env = _env()
    r = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=urllib.parse.urlencode({
            "username": env["SOCIAL_ADMIN_EMAIL"],
            "password": env["SOCIAL_ADMIN_PASSWORD"]}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    d = json.loads(urllib.request.urlopen(r, timeout=20).read())
    return d.get("access_token") or d.get("token")


def _connect(handle: str, app_password: str) -> None:
    tok = _login()
    r = urllib.request.Request(
        f"{API}/api/v1/bluesky/connect",
        data=json.dumps({"handle": handle, "app_password": app_password,
                         "team_id": TEAM}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {tok}"}, method="POST")
    try:
        d = json.loads(urllib.request.urlopen(r, timeout=30).read())
        print("wired:", json.dumps(d)[:200])
    except urllib.error.HTTPError as e:
        print("connect err:", e.code, e.read()[:200])


def from_password(handle: str, password: str) -> None:
    s, sess = _xrpc("com.atproto.server.createSession",
                    {"identifier": handle, "password": password})
    if s != 200:
        print("createSession failed:", s, str(sess)[:150])
        return
    jwt, did = sess["accessJwt"], sess["did"]
    s2, ap = _xrpc("com.atproto.server.createAppPassword",
                   {"name": "socialauto-" + secrets.token_hex(3)}, jwt=jwt)
    if s2 != 200:
        print("createAppPassword failed:", s2, str(ap)[:150])
        return
    app_pw = ap["password"]
    print("app password created:", app_pw)
    _connect(handle, app_pw)


def create(handle: str, email: str, phone=None, code=None) -> None:
    pw = "bsk" + secrets.token_urlsafe(18)
    body = {"handle": handle, "email": email, "password": pw}
    if phone: body["verificationPhone"] = phone
    if code: body["verificationCode"] = code
    s, d = _xrpc("com.atproto.server.createAccount", body)
    if s != 200:
        print("createAccount:", s, str(d)[:250])
        if "PhoneVerification" in str(d):
            print("→ rerun with --phone +30… and the SMS --code when it lands")
        return
    print("account created:", d.get("handle"), "| SAVE main password:", pw)
    jwt = d["accessJwt"]
    s2, ap = _xrpc("com.atproto.server.createAppPassword",
                   {"name": "socialauto"}, jwt=jwt)
    if s2 == 200:
        _connect(d["handle"], ap["password"])
    else:
        print("appPassword err:", s2, str(ap)[:120])


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("from-password")
    fp.add_argument("--handle", required=True)
    fp.add_argument("--password", required=True)
    cr = sub.add_parser("create")
    cr.add_argument("--handle", required=True)
    cr.add_argument("--email", required=True)
    cr.add_argument("--phone")
    cr.add_argument("--code")
    cn = sub.add_parser("connect")
    cn.add_argument("--handle", required=True)
    cn.add_argument("--app-password", required=True)
    a = ap.parse_args()
    if a.cmd == "from-password": from_password(a.handle, a.password)
    elif a.cmd == "create": create(a.handle, a.email, a.phone, a.code)
    else: _connect(a.handle, a.app_password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
