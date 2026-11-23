#!/usr/bin/env python3
"""ig_oauth_2fa_drive.py — complete an Instagram OAuth (instagram2 /
Instagram Business Login) flow that is stuck behind Meta's
``force_authentication`` wall: credential login -> 2FA method picker ->
code submit -> consent -> /oauth/instagram2/callback.

Verified live 2026-11-23 against instagram.com Bloks UI. Key facts learned
the hard way:

* ``instagram.com/oauth/authorize`` can bounce to
  ``/accounts/login/?force_authentication&next=<oauth url>`` even with a
  valid sessionid — the OAuth flow demands a FRESH credential login.
* 2FA defaults to WhatsApp/SMS. The method picker is Bloks-rendered:
  plain ``el.click()`` on rows does NOT switch the radio — you must
  ``label.click()`` the <label> wrapping the option text.
* The picker renders the option set multiple times (hidden duplicates);
  always pick the LAST visible ``label`` match.
* The dialog's Continue is the LOWEST visible ``div[role=button]``/
  ``button`` with text 'Continue' — there is also a form Continue behind
  the modal.
* TOTP secret may be stale after Meta security resets — the reliable lane
  is ``META_BACKUP_CODES`` in ``social_secrets`` (JSON array of one-time
  8-digit codes; consume one and write the remainder back).
* After 2FA, ``/consent/?flow=ig_biz_login_oauth`` shows Allow/Cancel —
  click Allow, then poll for ``instagram2/callback`` in any tab.
* CDP websocket needs ``suppress_origin=True`` when Chrome was launched
  without ``--remote-allow-origins`` (the ChromeDebug scheduled task
  profile). If :9229 refuses connections entirely, restart the
  ``ChromeDebug`` scheduled task on the Windows host first.

Usage:
    ig_oauth_2fa_drive.py                 # drive whatever stage is open
    ig_oauth_2fa_drive.py --from-login    # also do the password step
    ig_oauth_2fa_drive.py --code <8dig>   # use a specific backup code
    ig_oauth_2fa_drive.py --poll-callback # just wait for the callback

Requires: ~/.venv-igreset (websocket-client), docker access to
``social-api`` for INSTAGRAM_USERNAME/PASSWORD + META_BACKUP_CODES.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CDP = "http://192.168.96.1:9229"
CALLBACK_MARK = "instagram2/callback"


# ---------------------------------------------------------------- CDP helpers

def cdp_tabs(cdp: str = CDP) -> list[dict]:
    return json.loads(urllib.request.urlopen(cdp + "/json/list", timeout=15).read())


def ig_tabs() -> list[dict]:
    return [t for t in cdp_tabs()
            if t.get("type") == "page"
            and re.search(r"instagram\.com|instagram2/callback|cloudless\.gr/api/v1/auth",
                          t.get("url", ""))]


def current_tab() -> dict | None:
    """Best-guess active IG tab: deepest in the auth flow."""
    tabs = ig_tabs()
    if not tabs:
        return None
    priority = ["two_step_verification", "challenge", "consent",
                "accounts/login", "oauth/authorize", "oauth/instagram2/callback"]
    for key in priority:
        for t in reversed(tabs):
            if key in t.get("url", ""):
                return t
    return tabs[-1]


class Page:
    """CDP page handle that survives Bloks navigations."""

    def __init__(self, tab: dict):
        self.tab = tab
        self._connect()

    def _connect(self):
        import websocket  # type: ignore
        self.ws = websocket.create_connection(
            self.tab["webSocketDebuggerUrl"], timeout=25, suppress_origin=True)
        self._id = 0

    def reconnect(self):
        time.sleep(1.5)
        t = [x for x in cdp_tabs() if x["id"] == self.tab["id"]]
        if not t:
            c = ig_tabs()
            if not c:
                raise RuntimeError("no IG tab left")
            self.tab = c[-1]
        else:
            self.tab = t[0]
        try:
            self.ws.close()
        except Exception:
            pass
        self._connect()

    def cmd(self, method: str, params: dict | None = None):
        for attempt in range(2):
            try:
                self._id += 1
                self.ws.send(json.dumps({"id": self._id, "method": method,
                                         "params": params or {}}))
                while True:
                    msg = json.loads(self.ws.recv())
                    if msg.get("id") == self._id:
                        return msg
            except Exception:
                if attempt:
                    raise
                self.reconnect()

    def eval(self, expr: str):
        r = self.cmd("Runtime.evaluate", {"expression": expr,
                                          "returnByValue": True,
                                          "awaitPromise": True})
        return r.get("result", {}).get("result", {}).get("value")

    def click_xy(self, x: float, y: float):
        for t in ("mousePressed", "mouseReleased"):
            self.cmd("Input.dispatchMouseEvent",
                     {"type": t, "x": x, "y": y,
                      "button": "left", "clickCount": 1})
            time.sleep(0.07)

    def body(self, n: int = 500) -> str:
        return self.eval(f"document.body.innerText.slice(0,{n})") or ""


# ------------------------------------------------------------------- stages

def js_click_role_button(page: Page, text: str) -> bool:
    """JS-click the last visible div[role=button]/button with exact text."""
    r = page.eval(f"""(()=>{{
      const els=[...document.querySelectorAll('div[role=button],button')]
        .filter(e=>(e.innerText||'').trim()==={json.dumps(text)}&&e.offsetParent);
      if(!els.length)return 'NF';
      els[els.length-1].click();return 'OK';
    }})()""")
    return r == "OK"


def click_lowest_continue(page: Page):
    """Click the lowest visible 'Continue' — the modal's button sits below
    the underlying form's on IG's two-factor screens."""
    raw = page.eval("""(()=>{
      const els=[...document.querySelectorAll('div[role=button],button,div,span')]
        .filter(e=>(e.innerText||'').trim()==='Continue'&&e.children.length<4);
      const vis=els.map(e=>{const r=e.getBoundingClientRect();
        return {x:r.x+r.width/2,y:r.y+r.height/2,w:r.width}})
        .filter(q=>q.w>2&&q.y>0&&q.y<900);
      if(!vis.length)return null;
      vis.sort((a,b)=>b.y-a.y);return JSON.stringify(vis[0]);
    })()""")
    if raw:
        pt = json.loads(raw)
        page.click_xy(pt["x"], pt["y"])
        return True
    return js_click_role_button(page, "Continue")


def select_2fa_method(page: Page, method: str) -> bool:
    """Pick a method in the Bloks picker. ``label.click()`` is the only
    interaction that flips the radio — row divs and input.click() fail."""
    r = page.eval(f"""(()=>{{
      const ls=[...document.querySelectorAll('label')]
        .filter(l=>/{method}/i.test(l.innerText||'')&&l.offsetParent);
      if(!ls.length)return 'NOLBL';
      ls[ls.length-1].click();return 'OK';
    }})()""")
    if r != "OK":
        return False
    time.sleep(1.2)
    state = page.eval("""JSON.stringify([...document.querySelectorAll('input[type=radio]')]
      .map(r=>({c:r.checked,v:(r.closest('label')||r.parentElement).innerText.slice(0,16)})))""")
    # any checked radio whose label matches the method
    return bool(re.search(r'"c":true[^}]*' + re.escape(method[:8]), state or "", re.I))


def open_method_picker(page: Page) -> bool:
    body = page.body()
    if "confirmation methods" in body or "Choose a way" in body:
        return True
    if not js_click_role_button(page, "Try another way"):
        return False
    time.sleep(4)
    page.reconnect()
    return "Authentication app" in page.body() or "Backup code" in page.body()


def fill_visible_input(page: Page, value: str) -> bool:
    r = page.eval(f"""(()=>{{
      const ins=[...document.querySelectorAll('input')]
        .filter(i=>i.offsetParent&&(i.type==='text'||i.type==='tel'
          ||i.inputMode==='numeric'||i.autocomplete==='one-time-code'));
      if(!ins.length)return 'NOINPUT';
      const el=ins[ins.length-1];
      const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
      s.call(el,{json.dumps(value)});
      el.dispatchEvent(new Event('input',{{bubbles:true}}));
      el.dispatchEvent(new Event('change',{{bubbles:true}}));
      return 'OK';
    }})()""")
    return r == "OK"


def api_env(key: str) -> str:
    return subprocess.run(["docker", "exec", "social-api", "printenv", key],
                          capture_output=True, text=True, timeout=15).stdout.strip()


def api_secret_codes() -> list[str]:
    """META_BACKUP_CODES -> list of one-time codes (JSON array string)."""
    out = subprocess.run(
        ["docker", "exec", "-i", "social-api", "python", "-"],
        input=("import asyncio,json\n"
               "from sqlalchemy import text\n"
               "from app.db.session import async_session_maker\n"
               "from app.services.secret_store import decrypt_secret\n"
               "async def go():\n"
               "    async with async_session_maker() as db:\n"
               "        r=await db.execute(text(\"select value from social_secrets where key='META_BACKUP_CODES'\"))\n"
               "        v=r.scalar()\n"
               "    print(json.dumps(json.loads(decrypt_secret(v))))\n"
               "asyncio.run(go())\n"),
        capture_output=True, text=True, timeout=40).stdout
    # last JSON-looking line
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("["):
            try:
                return [str(c) for c in json.loads(line)]
            except Exception:
                pass
    return []


def consume_backup_code(code: str) -> bool:
    """Remove the used code from META_BACKUP_CODES (codes are single-use)."""
    script = (
        "import asyncio,json\n"
        "from sqlalchemy import text\n"
        "from app.db.session import async_session_maker\n"
        "from app.services.secret_store import decrypt_secret, encrypt_secret\n"
        "CODE=%r\n"
        "async def go():\n"
        "    async with async_session_maker() as db:\n"
        "        r=await db.execute(text(\"select value from social_secrets where key='META_BACKUP_CODES'\"))\n"
        "        v=r.scalar()\n"
        "    codes=json.loads(decrypt_secret(v))\n"
        "    codes=[c for c in codes if str(c)!=CODE]\n"
        "    async with async_session_maker() as db:\n"
        "        await db.execute(text(\"update social_secrets set value=:v where key='META_BACKUP_CODES'\"),{'v':encrypt_secret(json.dumps(codes))})\n"
        "        await db.commit()\n"
        "asyncio.run(go())\n" % code)
    r = subprocess.run(["docker", "exec", "-i", "social-api", "python", "-"],
                       input=script, capture_output=True, text=True, timeout=40)
    return r.returncode == 0


def totp_code(secret: str) -> str:
    import base64, hashlib, hmac, struct
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    dg = hmac.new(key, struct.pack(">Q", int(time.time()) // 30),
                  hashlib.sha1).digest()
    o = dg[-1] & 0xF
    return str((struct.unpack(">I", dg[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def wait_advance(tab_id: str, gone_ok: bool = True,
                 timeout: float = 150.0) -> str | None:
    """Poll until the tab URL leaves the auth flow. Returns final URL."""
    deadline = time.time() + timeout
    url = ""
    while time.time() < deadline:
        t = [x for x in cdp_tabs() if x["id"] == tab_id]
        if not t:
            if gone_ok:
                return None
            time.sleep(2)
            continue
        url = t[0]["url"]
        if CALLBACK_MARK in url or ("cloudless.gr" in url and "callback" in url):
            return url
        if not re.search(r"two_step|challenge|accounts/login|oauth/authorize|consent",
                         url):
            return url
        time.sleep(3)
    return url


# -------------------------------------------------------------------- driver

def do_login(page: Page) -> None:
    """Fill username+password on the force_authentication screen."""
    user, pw = api_env("INSTAGRAM_USERNAME"), api_env("INSTAGRAM_PASSWORD")
    if not user or not pw:
        raise RuntimeError("INSTAGRAM_USERNAME/PASSWORD missing in social-api env")
    r = page.eval(f"""(()=>{{
      function setV(el,v){{const s=Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,'value').set;
        s.call(el,v);el.dispatchEvent(new Event('input',{{bubbles:true}}));
        el.dispatchEvent(new Event('change',{{bubbles:true}}));}}
      const u=document.querySelector('input[name=username]');
      const p=document.querySelector('input[name=password]');
      if(!u||!p)return 'NOFIELDS';
      setV(u,{json.dumps(user)});setV(p,{json.dumps(pw)});return 'FILLED';
    }})()""")
    if r != "FILLED":
        raise RuntimeError("login fields not found: " + str(r))
    js_click_role_button(page, "Log in")


def do_2fa(page: Page, prefer: str = "auto") -> None:
    """Method picker -> code -> submit. prefer: totp|backup|auto."""
    open_method_picker(page)

    # TOTP first (doesn't consume a one-time code), backup as fallback.
    order = ["totp", "backup"] if prefer == "auto" else [prefer]
    last_err = ""
    for method in order:
        if method == "totp":
            secret = api_env("META_TOTP_SECRET_IG") or api_env("META_TOTP_SECRET")
            if not secret:
                continue
            label = "Authentication app"
            code = totp_code(secret)
        else:
            codes = api_secret_codes()
            if not codes:
                continue
            label = "Backup code"
            code = codes[0]

        if not select_2fa_method(page, label):
            last_err = f"{method}: picker select failed"
            continue
        click_lowest_continue(page)
        time.sleep(5)
        page.reconnect()

        body = page.body()
        if "authentication app" not in body.lower() and "backup code" not in body.lower() \
           and "code" not in body.lower():
            # maybe already advanced
            pass
        fill_visible_input(page, code)
        time.sleep(1)
        click_lowest_continue(page)

        # short verify window — wrong code shows 'invalid' quickly
        time.sleep(8)
        try:
            page.reconnect()
        except Exception:
            pass
        body = page.body().lower()
        if "invalid" not in body and "incorrect" not in body:
            return  # looks accepted; outer wait_advance confirms
        last_err = f"{method}: code rejected"
        # reopen picker for next method
        open_method_picker(page)
    raise RuntimeError("all 2FA methods failed: " + last_err)


def do_consent(page: Page) -> None:
    body = page.body()
    if "Allow" not in body:
        return
    r = page.eval("""(()=>{
      const els=[...document.querySelectorAll('button,div[role=button],a,span')]
        .filter(e=>(e.innerText||'').trim()==='Allow'&&e.offsetParent);
      if(!els.length)return 'NF';els[els.length-1].click();return 'OK';
    })()""")
    if r != "OK":
        js_click_role_button(page, "Allow")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-login", action="store_true",
                    help="also perform the credential login step")
    ap.add_argument("--prefer", choices=["auto", "totp", "backup"], default="auto")
    ap.add_argument("--poll-callback", action="store_true")
    ap.add_argument("--cdp", default=CDP)
    args = ap.parse_args()

    global CDP
    CDP = args.cdp

    tab = current_tab()
    if not tab:
        print("no instagram tab on CDP", file=sys.stderr)
        return 3
    print("[*] tab:", tab["url"][:120])
    page = Page(tab)

    url = tab["url"]
    if "force_authentication" in url or "accounts/login" in url:
        if not args.from_login:
            print("[*] at login wall — rerun with --from-login")
            return 4
        do_login(page)
        time.sleep(6)
        page.reconnect()
        url = page.tab["url"]

    if "two_step" in url or "challenge" in url:
        do_2fa(page, args.prefer)
        page.reconnect()
        url = page.tab["url"]

    if "consent" in url:
        do_consent(page)
        page.reconnect()

    if args.poll_callback or CALLBACK_MARK in (page.tab.get("url") or ""):
        cb = wait_advance(page.tab["id"], timeout=120)
        print("[+] callback:", (cb or "timeout/no-callback")[:200])
        return 0 if cb else 5
    print("[*] done — final url:", (page.tab.get("url") or "")[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
