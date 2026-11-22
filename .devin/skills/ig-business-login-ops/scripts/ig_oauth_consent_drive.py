#!/usr/bin/env python3
"""ig_oauth_consent_drive.py — inject stored IG browser_cookies into debug
Chrome (:9229), open the instagram2 authorize URL, click through consent,
watch for the oauth callback. One-shot; prints stage transitions only."""
import json, sys, time, urllib.request, urllib.parse

CDP = "http://192.168.96.1:9229"
API = "http://localhost:8000"


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=15).read())


def open_tab(url):
    req = urllib.request.Request(
        CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
        method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def api_py(code):
    import subprocess
    return subprocess.run(
        ["docker", "exec", "-i", "social-api", "python", "-c", code],
        capture_output=True, text=True, timeout=120).stdout


def get_cookies():
    code = r'''
import asyncio, json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
async def main():
    e = create_async_engine(settings.DATABASE_URL)
    async with e.connect() as c:
        r = await c.execute(text(
            "SELECT meta_data->'browser_cookies' FROM social_accounts "
            "WHERE platform='instagram' AND meta_data->>'login_type'='business_login' "
            "ORDER BY updated_at DESC LIMIT 1"))
        bc = r.fetchone()[0]
        print("JSONOUT" + json.dumps(bc or {}))
asyncio.run(main())
'''
    out = api_py(code)
    for line in out.splitlines():
        if line.startswith("JSONOUT"):
            return json.loads(line[7:])
    raise RuntimeError("cookie fetch failed: " + out[-300:])


def mint_auth_url():
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
    out = api_py(code)
    for line in out.splitlines():
        if line.startswith("URLOUT") and len(line) > 8:
            return line[6:]
    raise RuntimeError("auth url mint failed: " + out[-300:])


def ws_eval(ws_url, expr, mid=[0]):
    import websocket
    ws = websocket.create_connection(ws_url, timeout=25)
    mid[0] += 1
    ws.send(json.dumps({"id": mid[0], "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    ws.settimeout(20)
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == mid[0]:
            ws.close()
            return r.get("result", {}).get("result", {}).get("value")


def main():
    cookies = get_cookies()
    if not cookies:
        print("no browser_cookies stored"); return 2
    print("[*] cookies:", sorted(cookies.keys()))
    url = mint_auth_url()
    print("[*] auth url:", url[:110])

    # close stale callback tabs
    for t in tabs():
        if "instagram2/callback" in t.get("url", ""):
            try:
                urllib.request.urlopen(CDP + "/json/close/" + t["id"], timeout=10).read()
            except Exception:
                pass

    # seed cookies on an instagram.com tab
    seed = open_tab("https://www.instagram.com/")
    time.sleep(5)
    import websocket
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
            "path": "/", "secure": True,
            "httpOnly": name in ("sessionid", "ds_user_id")})
    ws.close()
    print("[*] cookies injected")

    tab = open_tab(url)
    print("[*] authorize tab:", tab["id"])
    deadline = time.time() + 240
    consent_tried = 0
    while time.time() < deadline:
        time.sleep(6)
        hit = None
        for t in tabs():
            u = t.get("url", "")
            if "instagram2/callback" in u:
                hit = ("CALLBACK", u); break
        if hit:
            print("[+] CALLBACK:", hit[1][:140]); return 0
        cur = ws_eval(tab["webSocketDebuggerUrl"], "location.href") or ""
        print("    ...", cur[:100])
        low = cur.lower()
        if ("authorize" in low or "consent" in low or "oauth" in low) and consent_tried < 4:
            body = ws_eval(tab["webSocketDebuggerUrl"], "document.body.innerText.slice(0,500)") or ""
            if re_search_allow(body):
                r = ws_eval(tab["webSocketDebuggerUrl"], """(()=>{
                  const b=[...document.querySelectorAll('button,[role=button],div[role=button]')]
                    .find(x=>/^(allow|authorize|continue)$/i.test((x.innerText||'').trim()));
                  if(!b)return{err:'no-btn'};b.click();return{clicked:(b.innerText||'').trim()};
                })()""")
                print("    consent click:", r); consent_tried += 1
                time.sleep(5)
        if "accounts/login" in low or "challenge" in low:
            print("[!] login/challenge wall — session cookies rejected")
            return 3
    print("[!] deadline — no callback")
    return 4


def re_search_allow(body):
    import re
    return re.search(r"allow|authorize|permission", body, re.I) is not None


if __name__ == "__main__":
    sys.exit(main())
