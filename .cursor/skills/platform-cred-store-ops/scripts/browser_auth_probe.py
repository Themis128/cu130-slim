#!/usr/bin/env python3
"""browser_auth_probe.py — battle-test platform auth state on debug Chrome.

Connects to the CDP relay (default http://192.168.96.1:9229, the :9229 debug
Chrome), inventories page tabs, and reports per-platform auth verdicts based on
VISIBLE page content — never cookie presence alone (injected cookies can be
stale/rejected; a profile-picker that won't complete = dead session).

Verdicts:
  AUTHENTICATED  — authenticated surface detected (timeline/feed/nav)
  LOGIN-WALL     — login form / logged-out landing
  PROFILE-PICKER — saved-profile chooser shown (one-tap); session MAY be dead —
                   use --click-continue to attempt the one-tap, then re-probe
  CHECKPOINT     — 2FA / "was this you" / verification wall
  UNKNOWN        — page didn't match any signature

Usage:
  browser_auth_probe.py                 # probe all tabs
  browser_auth_probe.py --click-continue  # also attempt profile-picker Continue
  browser_auth_probe.py --url https://x.com/home  # open+navigate a fresh tab probe
  browser_auth_probe.py --cdp http://192.168.96.1:9229

Read-only by default: evaluates JS, never submits passwords or clicks unless
--click-continue is passed. Requires `websocket-client` (use the igreset venv:
/home/tbaltzakis/.venv-igreset/bin/python).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import websocket  # websocket-client

# domain fragment -> (platform, authenticated regex, logged-out regex)
PLATFORMS = [
    ("x.com", "X", r"For you|Following|What’s happening|Home|Notifications",
     r"Continue with (phone|Google|Apple)|Email or username"),
    ("instagram.com", "Instagram",
     r"Create \(New Post\)|Suggested for you|Home ·|Your story",
     r"Log in|Sign up"),
    ("facebook.com", "Facebook",
     r"What’s on your mind|Home ·|facebook.com/watch",
     r"Log In|Email or phone"),
    ("threads.net", "Threads", r"For you|Threads", r"Log in"),
    ("linkedin.com", "LinkedIn", r"Start a post|My Network|Feed",
     r"Sign in|Join LinkedIn"),
    ("tiktok.com", "TikTok", r"For You|Following|Inbox",
     r"Log in|Sign up"),
    ("bsky.app", "Bluesky", r"Following|Starter Packs|chat.bsky",
     r"Sign in|Create account"),
]

PICKER_RE = r"Use another profile|Create new account"
CHECKPOINT_RE = (r"two.step verification|was this you|confirm your identity|"
                 r"verify your identity|unusual activity|suspicious login")


def list_pages(cdp: str) -> list[dict]:
    return [t for t in json.loads(
        urllib.request.urlopen(f"{cdp}/json/list", timeout=8).read())
        if t.get("type") == "page"]


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30,
                                              suppress_origin=True)
        self.mid = 0

    def call(self, method: str, **params):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method,
                                 "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.mid:
                return m.get("result", {})

    def eval(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


PROBE_JS = """JSON.stringify({
  url: location.href.slice(0,90),
  text: (document.body ? document.body.innerText : '').replace(/\\s+/g,' ').slice(0,3000)
})"""


def classify(platform: str, url: str, text: str) -> str:
    import re
    for frag, name, auth_re, out_re in PLATFORMS:
        if name != platform:
            continue
        if re.search(auth_re, text, re.I):
            return "AUTHENTICATED"
        if re.search(PICKER_RE, text, re.I) and "continue" in text.lower():
            return "PROFILE-PICKER"
        if re.search(CHECKPOINT_RE, text, re.I):
            return "CHECKPOINT"
        if re.search(out_re, text, re.I):
            return "LOGIN-WALL"
        return "UNKNOWN"
    return "NO-TAB"


def platform_for(url: str) -> str | None:
    for frag, name, _, _ in PLATFORMS:
        if frag in url:
            return name
    return None


def try_picker_continue(cdp: CDP) -> bool:
    """Dispatch click events on profile tile + Continue (one-tap login)."""
    js = """(()=>{
      const btns=[...document.querySelectorAll('[role=button],button')]
        .filter(e=>/^continue$/i.test((e.innerText||'').trim()));
      const tiles=[...document.querySelectorAll('div,li')]
        .filter(e=>{const t=(e.innerText||'').trim();
          return t.length>2 && t.length<60 && !/continue|another|account/i.test(t)
            && e.querySelector('img,[role=img]');});
      const fire=e=>['pointerdown','mousedown','pointerup','mouseup','click']
        .forEach(t=>e.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})));
      if(tiles.length) fire(tiles[tiles.length-1]);
      if(btns.length) fire(btns[btns.length-1]);
      return 'tiles='+tiles.length+' btns='+btns.length})()"""
    try:
        return "btns=0" not in (cdp.eval(js) or "")
    except Exception:
        return False


def probe_tab(tab: dict, click: bool) -> dict:
    plat = platform_for(tab["url"])
    if not plat:
        return {"url": tab["url"][:70], "platform": "-", "verdict": "SKIP"}
    out = {"url": tab["url"][:70], "platform": plat, "verdict": "ERR"}
    try:
        c = CDP(tab["webSocketDebuggerUrl"])
        raw = c.eval(PROBE_JS)
        data = json.loads(raw) if raw else {}
        out["verdict"] = classify(plat, data.get("url", ""), data.get("text", ""))
        out["snippet"] = data.get("text", "")[:110]
        if click and out["verdict"] == "PROFILE-PICKER":
            try_picker_continue(c)
            time.sleep(8)
            c.close()
            pages = list_pages(CDP_HOST)
            tab2 = next((t for t in pages
                         if platform_for(t.get("url", "")) == plat
                         and "password/reset" not in t.get("url", "")), None)
            if tab2:
                c2 = CDP(tab2["webSocketDebuggerUrl"])
                data2 = json.loads(c2.eval(PROBE_JS) or "{}")
                out["verdict"] = classify(plat, data2.get("url", ""),
                                          data2.get("text", "")) + " (after-continue)"
                c2.close()
        c.close()
    except Exception as e:  # websocket drop on navigation is normal
        out["verdict"] = "NAV-DROP" if click else f"ERR {type(e).__name__}"
    return out


CDP_HOST = "http://192.168.96.1:9229"


def main() -> int:
    global CDP_HOST
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default=CDP_HOST)
    ap.add_argument("--click-continue", action="store_true")
    ap.add_argument("--platform", help="only report this platform")
    args = ap.parse_args()
    CDP_HOST = args.cdp

    pages = list_pages(args.cdp)
    results = [probe_tab(t, args.click_continue) for t in pages]
    if args.platform:
        results = [r for r in results if r["platform"] == args.platform]
    for r in results:
        print(f"{r['platform']:<10} {r['verdict']:<22} {r['url']}")
        if r.get("snippet"):
            print(f"{'':<10} ↳ {r['snippet']}")
    bad = [r for r in results
           if r["verdict"] not in ("AUTHENTICATED", "SKIP")]
    print(f"\n{len(results)} tabs, {len(bad)} need attention")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
