#!/usr/bin/env python3
"""fb_reauth.py — mint the Facebook OAuth consent URL for the dead FB token,
drive it on the debug Chrome via CDP, complete the consent → fresh
token → the dead-token heal flag clears.

CDP lanes tried in order: the :9229 userspace relay (chrome-debug-profile,
what the ChromeDebug logon task actually binds — :9222 is claimed by a
Lenovo WebView2 on this host), then localhost:9222 for older setups.
Opens its OWN tab via /json/new — never hijacks an existing page.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

CDP_CANDIDATES = ["http://172.22.144.1:9229", "http://localhost:9229",
                  "http://localhost:9222"]
CALLBACK_MARK = "social.cloudless.gr/api/v1/auth/oauth/facebook/callback"


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
    import shutil
    import subprocess
    docker = ("/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/"
              "resources/bin/docker.exe")
    if not os.path.exists(docker):
        docker = shutil.which("docker") or "docker"
    inner = (
        "import asyncio\n"
        "from app.core.security import sign_oauth_state\n"
        "from app.db.session import get_db\n"
        "from sqlalchemy import text\n"
        "async def go():\n"
        "  async for db in get_db():\n"
        "    r=await db.execute(text('SELECT id FROM teams LIMIT 1'))\n"
        "    print(sign_oauth_state({'t':str(r.scalar())}))\n"
        "asyncio.run(go())"
    )
    out = subprocess.run(
        [docker, "exec", "social-api", "python", "-c", inner],
        capture_output=True, text=True, timeout=30, check=False)
    for line in out.stdout.splitlines():
        if line.strip() and not line.startswith(("TEAM", "ERR")):
            return line.strip()
    raise RuntimeError(f"signed state failed: {out.stdout} {out.stderr}")


def _auth_url():
    e = _env()
    cid = e.get("FACEBOOK_CLIENT_ID", "") or e.get("META_APP_ID", "")
    red = e.get("FACEBOOK_REDIRECT_URI", "") or e.get(
        "META_REDIRECT_URI",
        "https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback")
    scopes = ("pages_manage_posts,pages_read_engagement,pages_show_list,"
              "instagram_basic,instagram_content_publish,"
              "business_management,public_profile")
    return (f"https://www.facebook.com/v21.0/dialog/oauth?client_id={cid}"
            f"&redirect_uri={urllib.parse.quote(red,safe='')}"
            f"&response_type=code&scope={urllib.parse.quote(scopes)}"
            f"&state={urllib.parse.quote(_signed_state(),safe='')}")


# Click the FB consent buttons (Continue as / Authorize / Save). FB's OAuth
# dialog is a React SPA — poll until the button exists.
_CLICK_JS = """
(() => {
  const els = [...document.querySelectorAll(
    'button,[role=button],input[type=submit],a[role=link]')];
  const b = els.find(e => /continue as|authorize|allow|save|continue/i
    .test((e.innerText||e.value||e.getAttribute('aria-label')||'').trim())
    && (e.innerText||e.value||'').trim().length < 50
    && !/cancel|not now|decline|deny|learn more/i.test(e.innerText||''));
  if (b) { b.click(); return 'clicked:' + (b.innerText||b.value||'').trim().slice(0,40); }
  return 'no-button';
})()
"""

_STATE_JS = """
(() => JSON.stringify({
  url: location.href.slice(0, 160),
  title: document.title,
  text: (document.body ? document.body.innerText : '').slice(0, 300)
}))()
"""


def _cdp_base():
    for base in CDP_CANDIDATES:
        try:
            tabs = json.loads(urllib.request.urlopen(
                base + "/json", timeout=5).read())
            if tabs:
                return base
        except Exception:
            continue
    return None


def _open_tab(base):
    # about:blank first — very long OAuth URLs occasionally fail to load
    # through /json/new?<url>; Page.navigate over the ws is reliable.
    req = urllib.request.Request(base + "/json/new?about:blank",
                                 method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _close_tab(base, tab_id):
    try:
        urllib.request.urlopen(base + "/json/close/" + tab_id, timeout=5)
    except Exception:
        pass


def _ws_eval(ws_url, js, mid=1, method=None, params=None):
    import websocket
    ws = websocket.create_connection(ws_url, suppress_origin=True,
                                     timeout=20)
    try:
        if method:
            ws.send(json.dumps({"id": mid, "method": method,
                                "params": params or {}}))
        else:
            ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                                "params": {"expression": js,
                                           "returnByValue": True,
                                           "awaitPromise": True}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == mid:
                return str(r.get("result", {}).get("result", {})
                           .get("value", ""))
    finally:
        ws.close()


def main() -> int:
    argv = sys.argv
    if "--url" in argv:
        print(_auth_url())
        return 0
    base = _cdp_base()
    if not base:
        print("CDP not bound — tried " + ", ".join(CDP_CANDIDATES))
        return 1
    url = _auth_url()
    tab = _open_tab(base)
    ws_url = tab.get("webSocketDebuggerUrl", "")
    _ws_eval(ws_url, None, mid=0, method="Page.enable")
    _ws_eval(ws_url, None, mid=0, method="Page.navigate",
             params={"url": url})
    print(f"cdp={base} tab={tab.get('id','')[:8]} — driving consent")
    deadline = time.time() + 150
    last = ""
    while time.time() < deadline:
        try:
            state = _ws_eval(ws_url, _STATE_JS)
        except Exception as e:
            state = json.dumps({"url": f"eval-error:{e}"})
        try:
            st = json.loads(state)
        except Exception:
            st = {"url": state[:160]}
        cur = st.get("url", "")
        if cur != last:
            print("page:", cur[:110])
            last = cur
        if CALLBACK_MARK in cur or "facebook.com" not in cur:
            break
        if "/login" in cur or "checkpoint" in cur:
            print("facebook session logged out — human login needed "
                  "in the debug browser first")
            break
        try:
            click = _ws_eval(ws_url, _CLICK_JS)
            if click and click != "no-button" and click != last + "x":
                print(" ", click)
        except Exception:
            pass
        time.sleep(4)
    try:
        final = json.loads(_ws_eval(ws_url, _STATE_JS))
    except Exception:
        final = {}
    fu = final.get("url", "")
    print("final:", fu[:160])
    ok = CALLBACK_MARK in fu or ("social.cloudless.gr" in fu
                                 and "error" not in fu.lower())
    _close_tab(base, tab.get("id", ""))
    print("reauth", "callback-landed" if ok else "needs-human")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
