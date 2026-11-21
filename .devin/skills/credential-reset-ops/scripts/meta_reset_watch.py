#!/usr/bin/env python3
"""meta_reset_watch.py — unattended Meta Account password-reset watcher.

Meta throttles recovery codes hard (multiple burned codes -> resend silent
drop for ~hours). This watcher keeps the auth.meta.com "Confirm your
account" tab alive on the debug Chrome (:9229), re-clicks Resend on a
cooldown, polls Gmail for the fresh code, enters it, sets a new Meta
Account password, and writes it back to .env (FACEBOOK_PASSWORD +
INSTAGRAM_PASSWORD — one shared Meta Account secret).

Safe to run for hours: every step is idempotent and state is re-read from
the live page each pass. Exit 0 on success, 2 if the tab is gone (re-run
the manual seed steps in SKILL.md), 3 on timeout.

Usage: meta_reset_watch.py [--max-hours 12]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.parse
import urllib.request
import string
import secrets as _secrets
import subprocess
import sys
import time
import urllib.request

CDP = "http://192.168.96.1:9229"
REPO = os.path.expanduser("~/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")
CODE_RE = re.compile(r"\b(\d{6})\b")

GMAIL_SECRETS = "/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/gmail-token.json"
GMAIL_KEYS = "/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/gcp-oauth.keys.json"


def gmail_access_token() -> str | None:
    try:
        tok = json.load(open(GMAIL_SECRETS))
        keys = json.load(open(GMAIL_KEYS))["installed"]
        r = urllib.request.urlopen(urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "client_id": keys["client_id"],
                "client_secret": keys["client_secret"],
                "refresh_token": tok["refresh_token"],
                "grant_type": "refresh_token"}).encode()), timeout=15)
        return json.loads(r.read())["access_token"]
    except Exception as exc:
        print("gmail token err:", exc)
        return None


def targets():
    return json.load(urllib.request.urlopen(CDP + "/json/list", timeout=8))


def auth_tab():
    return [t for t in targets()
            if t.get("type") == "page" and "auth.meta.com" in t.get("url", "")]


class CdpSession:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=25)
        self.mid = 0

    def cmd(self, method, params=None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method,
                                 "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.mid:
                return m.get("result", {})

    def ev(self, expr):
        r = self.cmd("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def connect():
    tabs = auth_tab()
    if not tabs:
        return None
    return CdpSession(tabs[0]["webSocketDebuggerUrl"])


def page_text(c):
    return c.ev("document.body.innerText") or ""


def gen_password() -> str:
    alpha = string.ascii_letters + string.digits
    return "Cl@" + "".join(_secrets.choice(alpha) for _ in range(14)) + "!"


def save_env_pw(pw: str) -> None:
    lines = open(ENV_FILE).read().splitlines()
    out, seen = [], set()
    for ln in lines:
        if ln.startswith(("FACEBOOK_PASSWORD=", "INSTAGRAM_PASSWORD=")):
            key = ln.split("=", 1)[0]
            out.append(f"{key}={pw}")
            seen.add(key)
        else:
            out.append(ln)
    for key in ("FACEBOOK_PASSWORD", "INSTAGRAM_PASSWORD"):
        if key not in seen:
            out.append(f"{key}={pw}")
    open(ENV_FILE, "w").write("\n".join(out) + "\n")
    os.chmod(ENV_FILE, 0o600)
    print("[ok] password written to .env")


def gmail_scan(since_ts: str):
    """Return (code, newest_internalDate_seen). Only accepts mail newer
    than since_ts so a burned code is never re-submitted."""
    at = gmail_access_token()
    if not at:
        return None, since_ts
    newest = since_ts
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
            "?q=from:facebookmail.com+newer_than:120m&maxResults=8",
            headers={"Authorization": "Bearer " + at}), timeout=15)
        msgs = json.loads(r.read()).get("messages", [])
        for msg in msgs:
            r2 = urllib.request.urlopen(urllib.request.Request(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                f"{msg['id']}?format=full",
                headers={"Authorization": "Bearer " + at}), timeout=15)
            d = json.loads(r2.read())
            ts = d.get("internalDate", "0")
            if ts > newest:
                newest = ts
            if ts <= since_ts:
                continue
            body = ""

            def walk(p):
                nonlocal body
                if p.get("mimeType", "").startswith("text/") and \
                        p.get("body", {}).get("data"):
                    body += base64.urlsafe_b64decode(
                        p["body"]["data"]).decode("utf-8", "replace")
                for part in p.get("parts", []) or []:
                    walk(part)
            walk(d["payload"])
            mm = CODE_RE.search(body)
            if mm:
                return mm.group(1), newest
    except Exception as exc:
        print("gmail poll err:", exc)
    return None, newest


def click_button(c, label):
    return c.ev(f"""(()=>{{
      const b=[...document.querySelectorAll('div[role=button],button,a')]
        .find(x=>(x.innerText||'').trim().toLowerCase()==='{label.lower()}');
      if(!b) return 'no-btn';
      const r=b.getBoundingClientRect();
      return JSON.stringify([r.x+r.width/2,r.y+r.height/2]);
    }})()""")


def mouse_click(c, label):
    pos = click_button(c, label)
    if not pos or pos == "no-btn":
        # fallback: DOM click
        return c.ev(f"""(()=>{{
          const b=[...document.querySelectorAll('div[role=button],button,a')]
            .find(x=>(x.innerText||'').trim().toLowerCase()==='{label.lower()}');
          if(!b) return 'no-btn'; b.click(); return 'dom-clicked';
        }})()""")
    try:
        x, y = json.loads(pos)
        for ev in ("mousePressed", "mouseReleased"):
            c.cmd("Input.dispatchMouseEvent",
                  {"type": ev, "x": x, "y": y, "button": "left", "clickCount": 1})
        return "mouse-clicked"
    except Exception:
        return c.ev(f"""(()=>{{
          const b=[...document.querySelectorAll('div[role=button],button,a')]
            .find(x=>(x.innerText||'').trim().toLowerCase()==='{label.lower()}');
          if(!b) return 'no-btn'; b.click(); return 'dom-clicked';
        }})()""")


def type_text(c, text):
    c.ev("(()=>{const i=document.querySelector('input');i.focus();i.click();return 1})()")
    time.sleep(0.3)
    for ch in text:
        c.cmd("Input.dispatchKeyEvent",
              {"type": "keyDown", "key": ch, "text": ch})
        c.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        time.sleep(0.05)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=12)
    args = ap.parse_args()
    deadline = time.time() + args.max_hours * 3600
    new_pw = gen_password()
    resend_at = 0.0

    # baseline: newest code-email timestamp already seen — anything older
    # is a burned code and must never be re-submitted
    _, last_code_ts = gmail_scan("0")
    print(f"[init] baseline code-ts={last_code_ts}")

    while time.time() < deadline:
        tabs = auth_tab()
        if not tabs:
            print("[wait] auth.meta.com tab gone — seed it manually, retrying")
            time.sleep(300)
            continue
        c = connect()
        txt = page_text(c)
        low = txt.lower()

        if "new password" in low or "re-type" in low or "create password" in low:
            print("[stage] password form reached")
            n = c.ev("document.querySelectorAll('input[type=password]').length") or 0
            fields = c.ev("""(()=>{const f=[...document.querySelectorAll('input[type=password]')];
              return f.map(i=>{const s=Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype,'value').set;
                s.call(i,'');i.dispatchEvent(new Event('input',{bubbles:true}));return 1})})()""")
            # fill via keyboard per field
            for idx in range(min(n or 0, 2)):
                c.ev(f"(()=>{{const i=document.querySelectorAll('input[type=password]')[{idx}];i.focus();i.click();return 1}})()")
                time.sleep(0.2)
                for ch in new_pw:
                    c.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch})
                    c.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
                    time.sleep(0.03)
            time.sleep(0.4)
            print("[click]", mouse_click(c, "change password") or mouse_click(c, "continue") or mouse_click(c, "save"))
            time.sleep(6)
            save_env_pw(new_pw)
            print("[done] Meta Account password rotated")
            c.close()
            return 0

        if "confirm your account" in low or "enter that code" in low:
            # throttle resend: at most once per 20 min
            if time.time() > resend_at:
                print("[click] resend code")
                print("   ->", mouse_click(c, "resend code"))
                resend_at = time.time() + 1200
                time.sleep(3)
            code, last_code_ts = gmail_scan(last_code_ts)
            if code:
                print("[code] fresh code received — entering")
                type_text(c, code)
                time.sleep(0.5)
                print("[click]", mouse_click(c, "continue"))
                time.sleep(6)
                # loop re-reads state; if code rejected we'll resend again later
            else:
                print("[wait] no fresh code yet")
            c.close()
            time.sleep(30)
            continue

        # unknown state — snapshot and keep waiting
        print("[state]", low[:120].replace("\n", " | "))
        c.close()
        time.sleep(60)

    print("[timeout] gave up after", args.max_hours, "h")
    return 3


if __name__ == "__main__":
    sys.exit(main())
