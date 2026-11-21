#!/usr/bin/env python3
"""ig_bl_session_drive.py — revive the instagram2 (business_login) lane.

Root cause this fixes: the fb_onboarding IG row loses
``instagram_business_account`` on its FB Page (Meta Graph bug / integrity
event) → every /media call returns OAuthException #10 even though token
scopes look granted. The instagram2 lane (graph.instagram.com,
instagram_business_* scopes) does NOT need the Page link.

Flow (all inside SocialAuto, no password/2FA needed when a private-API
session blob exists):
  1. Pull sessionid + ds_user_id from social_accounts.meta_data
     (private_api_session_id + private_api_session_settings, both
     decrypt_field-encrypted) — runs inside social-api container.
  2. Inject cookies into a CDP Chrome (:9229 debug or sidecar) via
     Network.setCookie on .instagram.com.
  3. Open the instagram2 authorize URL (INSTAGRAM2_CLIENT_ID/SECRET from
     .env — the Instagram App ID, NOT the Meta app id).
  4. IG renders consent → redirect hits /oauth/instagram2/callback →
     social-api exchanges code → business_login row flips active.
  5. Verify: probe graph.instagram.com/{ig-user}/media container.

Prereqs:
  - A live IG session blob on the instagram social_accounts row
    (instagram-ops / ig_session_blob.py maintains it).
  - INSTAGRAM2_CLIENT_ID/SECRET + redirect URI configured in Meta app
    ("Instagram > API setup with Instagram login") and present in the
    compose env file (deploy/socialauto-standalone/.env).

Run on host:
  python3 ig_bl_session_drive.py --cdp http://<ws-ip>:9229
  python3 ig_bl_session_drive.py --cdp http://localhost:9226 --sidecar
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

IG_ROW_ID = "d2215106-7e54-4c43-a5f4-383b1ae0c61e"  # fb_onboarding cloudless.gr
API_CONTAINER = "social-api"


def _ws_ip() -> str:
    try:
        out = subprocess.run(
            ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return out or "172.22.144.1"
    except Exception:
        return "172.22.144.1"


def tabs(cdp: str):
    return json.loads(urllib.request.urlopen(cdp + "/json/list", timeout=15).read())


def open_tab(cdp: str, url: str) -> dict:
    req = urllib.request.Request(cdp + "/json/new?" + urllib.parse.quote(url, safe=":/?&=%,.{}"), method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def api_python(code: str) -> str:
    """Run python inside social-api; returns stdout."""
    r = subprocess.run(["docker", "exec", "-i", API_CONTAINER, "python", "-"],
                       input=code, capture_output=True, text=True, timeout=120)
    return r.stdout


def extract_ig_cookies() -> dict:
    """Decrypt session cookies from the instagram row's meta_data."""
    code = r'''
import asyncio, json
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_field
ROW = "''' + IG_ROW_ID + '''"
async def main():
    async with async_session_maker() as db:
        r = (await db.execute(select(SocialAccount).where(SocialAccount.id == ROW))).scalar_one()
        md = r.meta_data or {}
        out = {}
        sid = md.get("private_api_session_id")
        if sid:
            try: out["sessionid"] = decrypt_field(sid)
            except Exception: out["sessionid"] = sid
        blob_enc = md.get("private_api_session_settings")
        if blob_enc:
            try:
                blob = json.loads(decrypt_field(blob_enc))
                ck = blob.get("cookies") or {}
                if isinstance(ck, dict):
                    for k in ("ds_user_id","csrftoken","mid","ig_did","datr","rur"):
                        if ck.get(k): out[k] = ck[k]
                else:
                    for c in ck:
                        n = c.get("name")
                        if n in ("ds_user_id","csrftoken","mid","ig_did","datr","rur"):
                            out[n] = c.get("value")
            except Exception:
                pass
        print("JSONOUT" + json.dumps(out))
asyncio.run(main())
'''
    out = api_python(code)
    for line in out.splitlines():
        if line.startswith("JSONOUT"):
            return json.loads(line[7:])
    raise RuntimeError("cookie extract failed: " + out[-300:])


def mint_auth_url() -> str:
    """Ask social-api for the instagram2 authorize URL (needs INSTAGRAM2_* env)."""
    code = r'''
import asyncio, os, httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import create_access_token
TEAM = "10024d25-2ea4-40ea-86c6-ba89f515a2eb"
async def main():
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.connect() as c:
        uid = await c.scalar(text("select id from users order by created_at limit 1"))
    t = create_access_token({"sub": str(uid)})
    r = httpx.get("http://localhost:8000/api/v1/auth/oauth/instagram2/authorize",
                  params={"team_id": TEAM}, headers={"Authorization": "Bearer " + t}, timeout=30)
    print("URLOUT" + r.json().get("authorization_url", ""))
asyncio.run(main())
'''
    out = api_python(code)
    for line in out.splitlines():
        if line.startswith("URLOUT") and len(line) > 8:
            return line[6:]
    raise RuntimeError("authorize mint failed (INSTAGRAM2_* missing in container env?): " + out[-300:])


def cdp_eval(ws_url: str, expr: str):
    import websocket  # type: ignore
    ws = websocket.create_connection(ws_url, timeout=20)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    ws.settimeout(15)
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == 1:
            ws.close()
            return r.get("result", {}).get("result", {}).get("value")


def inject_cookies_and_drive(cdp: str, auth_url: str, cookies: dict, wait_s: int = 90):
    """Open authorize URL in a new tab, set IG cookies first via a blank tab."""
    import websocket  # type: ignore
    # Seed cookies on instagram.com first
    seed = open_tab(cdp, "https://www.instagram.com/")
    time.sleep(4)
    ws = websocket.create_connection(seed["webSocketDebuggerUrl"], timeout=20)
    mid = [0]

    def cmd(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        ws.settimeout(15)
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == mid[0]:
                return r

    cmd("Network.enable")
    for name, val in cookies.items():
        if not val:
            continue
        cmd("Network.setCookie", {
            "name": name, "value": str(val), "domain": ".instagram.com",
            "path": "/", "secure": True, "httpOnly": name == "sessionid"})
    ws.close()
    # Now open the authorize URL — session is live, consent should render
    tab = open_tab(cdp, auth_url)
    print("[*] authorize tab:", tab.get("id"))
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(5)
        for t in tabs(cdp):
            u = t.get("url", "")
            if "instagram2/callback" in u or "oauth/instagram/callback" in u:
                print("[+] callback reached:", u[:120])
                return u
        cur = cdp_eval(tab["webSocketDebuggerUrl"], "location.href")
        print("    ...", (cur or "")[:90])
    print("[!] no callback within wait window — check tab manually")
    return None


def verify_business_login() -> bool:
    code = r'''
import asyncio, json, urllib.request, urllib.error
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
async def main():
    async with async_session_maker() as db:
        r = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform == "instagram",
            SocialAccount.meta_data["login_type"].astext == "business_login"))).scalars().first()
        tok = decrypt_token(bytes(r.access_token_enc))
        uid = r.account_id
    try:
        resp = json.loads(urllib.request.urlopen(
            "https://graph.instagram.com/v21.0/me?fields=user_id,username&access_token=" + tok,
            timeout=15).read())
        print("VERIFY", r.status, resp.get("username"), resp.get("user_id"))
    except urllib.error.HTTPError as e:
        print("VERIFY_FAIL", e.code, e.read()[:200])
asyncio.run(main())
'''
    out = api_python(code)
    for line in out.splitlines():
        if line.startswith("VERIFY"):
            print(line)
            return "VERIFY_FAIL" not in line
    print(out[-300:])
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default=None, help="CDP base e.g. http://192.168.96.1:9229")
    ap.add_argument("--sidecar", action="store_true", help="use facebook-browser-sidecar debug API instead of raw CDP")
    ap.add_argument("--skip-drive", action="store_true", help="only verify current business_login row")
    args = ap.parse_args()

    if args.skip_drive:
        sys.exit(0 if verify_business_login() else 1)

    cdp = args.cdp or ("http://" + _ws_ip() + ":9229")
    print("[*] CDP:", cdp)
    cookies = extract_ig_cookies()
    print("[*] cookies:", sorted(cookies.keys()))
    if "sessionid" not in cookies:
        raise SystemExit("no sessionid — run instagram-ops session heal first")
    url = mint_auth_url()
    print("[*] auth url minted")
    cb = inject_cookies_and_drive(cdp, url, cookies)
    if cb:
        time.sleep(8)  # let callback exchange finish
        ok = verify_business_login()
        print("[+] business_login verified" if ok else "[!] verify failed — check social-api logs")
        sys.exit(0 if ok else 1)
    sys.exit(2)


if __name__ == "__main__":
    main()
