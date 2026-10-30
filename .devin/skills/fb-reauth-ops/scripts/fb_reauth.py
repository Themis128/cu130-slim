#!/usr/bin/env python3
"""fb_reauth.py — mint the Facebook OAuth consent URL for the dead FB token,
drive it on the bound debug Chrome (:9222), complete the consent → fresh
token → the dead-token heal flag clears."""
import json
import time
import urllib.parse
import urllib.request

CDP = "http://localhost:9222"

def _env():
    d = {}
    for line in open("/home/tbaltzakis/cu130-slim/.env").read().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"')
    return d

def _signed_state() -> str:
    """Mint a properly-signed OAuth state via the backend's sign_oauth_state —
    fabricated strings fail the app's signature check."""
    import subprocess
    out = subprocess.run(
        ["docker","exec","social-api","python","-c",
         "import asyncio\n"
         "from app.core.security import sign_oauth_state\n"
         "from app.db.session import get_db\n"
         "from sqlalchemy import text\n"
         "async def go():\n"
         "  async for db in get_db():\n"
         "    r=await db.execute(text('SELECT id FROM teams LIMIT 1'))\n"
         "    print(sign_oauth_state({'t':str(r.scalar())}))\n"
         "asyncio.run(go())"],
        capture_output=True, text=True, timeout=30)
    for line in out.stdout.splitlines():
        if line.strip() and not line.startswith(("TEAM","ERR")):
            return line.strip()
    raise RuntimeError(f"signed state failed: {out.stdout} {out.stderr}")

def _auth_url():
    e = _env()
    cid = e.get("FACEBOOK_CLIENT_ID", "") or e.get("META_APP_ID", "")
    red = e.get("FACEBOOK_REDIRECT_URI", "") or e.get("META_REDIRECT_URI", "https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback")
    scopes = "pages_manage_posts,pages_read_engagement,pages_show_list,instagram_basic,instagram_content_publish,business_management,public_profile"
    return (f"https://www.facebook.com/v21.0/dialog/oauth?client_id={cid}"
            f"&redirect_uri={urllib.parse.quote(red,safe='')}"
            f"&response_type=code&scope={urllib.parse.quote(scopes)}"
            f"&state={urllib.parse.quote(_signed_state(),safe='')}")

def _ws():
    try:
        d = json.loads(urllib.request.urlopen(f"{CDP}/json", timeout=4).read())
        for t in d:
            if t.get("type") == "page":
                return t.get("webSocketDebuggerUrl")
    except Exception:
        return None

def _eval(ws_url, js):
    import websocket
    ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=15)
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":js,"returnByValue":True}}))
    r = json.loads(ws.recv())
    ws.close()
    return str(r.get("result",{}).get("result",{}).get("value",""))

def main() -> int:
    if "--url" in __import__("sys").argv:
        print(_auth_url())
        return 0
    ws = _ws()
    if not ws:
        print("CDP not bound — run chrome-debug-launch (or wait for the logon task)")
        return 1
    _eval(ws, f'location.href={json.dumps(_auth_url())}')
    time.sleep(8)
    print("page:", _eval(ws, "location.href.slice(0,80)"))
    return 0

if __name__ == "__main__":
    __import__("sys").exit(main())
