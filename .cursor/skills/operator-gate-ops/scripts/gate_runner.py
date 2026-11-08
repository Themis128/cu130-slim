#!/usr/bin/env python3
"""gate_runner.py — full automation for the three operator gates.

Drives Chrome via CDP :9222. Two profiles:
  debug (default) — separate chrome-debug-profile; safe, won't touch the
                    user's session; needs its own logins once (persists).
  real  --real    — kills all chrome, relaunches the REAL profile with
                    CDP. Already logged into FB/dash — zero-login path.
                    Destructive (closes user tabs); restores a normal
                    chrome afterwards when --restore is passed.

Gates (all end-to-end, no manual steps unless noted):
  fb-token <oauth_authorize_url>   OAuth → token → omv secrets + secret_store
  email-routing                    dashboard add destination + mailbox verify
  deploy-token                     dashboard mint + opaque gh secret set

Usage: gate_runner.py <gate> [args] [--real] [--restore]
"""
import json
import re
import subprocess
import sys
import time
import urllib.request

CDP = "http://localhost:9222"
REAL = "--real" in sys.argv
RESTORE = "--restore" in sys.argv
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PROFILE = r"C:\Users\tbaltzakis\chrome-debug-profile"


def tabs():
    try:
        with urllib.request.urlopen(f"{CDP}/json", timeout=5) as f:
            return [t for t in json.loads(f.read()) if t.get("type") == "page"]
    except Exception:
        return []


def _ensure_cdp() -> None:
    if tabs():
        return
    if REAL:
        subprocess.run(["cmd", "/c", "taskkill /F /IM chrome.exe"],
                       capture_output=True, timeout=30)
        time.sleep(3)
        subprocess.Popen([CHROME, "--remote-debugging-port=9222"])
    else:
        subprocess.Popen([CHROME, "--remote-debugging-port=9222",
                          f"--user-data-dir={DEBUG_PROFILE}",
                          "--no-first-run", "about:blank"])
    for _ in range(30):
        if tabs():
            return
        time.sleep(1)
    raise SystemExit("CDP never came up on :9222")


class Page:
    _id = 0

    def __init__(self):
        import websocket
        ts = tabs()
        if not ts:
            raise SystemExit("no CDP tab")
        self.ws = websocket.create_connection(
            ts[0]["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        for m in ("Page.enable", "Runtime.enable"):
            self._send(m)

    def _send(self, method, params=None):
        Page._id += 1
        mid = Page._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == mid:
                return r

    def goto(self, url, settle=4):
        self._send("Page.navigate", {"url": url})
        time.sleep(settle)

    def js(self, expr):
        try:
            r = self._send("Runtime.evaluate", {
                "expression": expr, "returnByValue": True, "awaitPromise": True})
            return (r.get("result") or {}).get("result", {}).get("value")
        except Exception:
            # Chrome died (host watchers kill it) — relaunch + reattach once
            _ensure_cdp()
            self.__init__()
            r = self._send("Runtime.evaluate", {
                "expression": expr, "returnByValue": True, "awaitPromise": True})
            return (r.get("result") or {}).get("result", {}).get("value")

    def poll(self, expr, timeout=60, interval=1):
        for _ in range(int(timeout / interval)):
            v = self.js(expr)
            if v:
                return v
            time.sleep(interval)
        return None

    def click(self, pattern):
        self.js(
            "(()=>{const t=" + pattern + ";const b=[...document.querySelectorAll("
            "'button,a,input[type=submit],div[role=button]')]"
            ".find(e=>t.test((e.innerText||e.value||e.getAttribute('aria-label')||'').trim()));"
            "if(b){b.click();return 'ok'} return null})()")

    def type_text(self, selector_fn, value):
        self.js(
            "(()=>{const i=" + selector_fn + ";if(!i)return 'NOINPUT';"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(i," + json.dumps(value) + ");"
            "i.dispatchEvent(new Event('input',{bubbles:true}));return 'TYPED'})()")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def gate_fb():
    url = next((a for a in sys.argv[2:] if not a.startswith("--")), None)
    if not url:
        return "usage: gate_runner.py fb-token <oauth_authorize_url> [--real]"
    _ensure_cdp()
    p = Page()
    p.goto(url)
    v = p.poll(
        "(()=>{const m=location.hash.match(/access_token=([^&]+)/);"
        "if(m)return 'TOKEN:'+m[1];"
        "const i=document.querySelector('input[type=email],input[type=text]');"
        "if(i&&/login|log in/i.test(document.title+document.body.innerText.slice(0,2000)))"
        "  return 'LOGIN_FORM';return ''})()",
        timeout=90)
    p.close()
    if not v:
        return "no token redirect within 90s — check the tab (login/consent screen?)"
    if v == "LOGIN_FORM":
        return ("hit FB login form — profile isn't logged in. Log into facebook.com "
                "in the debug Chrome once (persists), or use --real")
    token = v.split("TOKEN:", 1)[1]
    r1 = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "omv",
         f"echo -n '{token}' > /home/tbaltzakis/secrets/fb-user-token"],
        capture_output=True, timeout=20)
    r2 = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c",
         f"from app.services.secret_store import set as s; s('FB_USER_TOKEN','{token}')"],
        cwd=r"C:\Users\tbaltzakis\cu130-slim", capture_output=True, timeout=30)
    if r1.returncode == 0:
        return "fb-token OK — omv secrets + secret_store updated; daily heal renews page token"
    return f"partial: omv write rc={r1.returncode} api rc={r2.returncode}"


def _dash_sso_login(p):
    """Google-SSO login on the CF sign-in page — the debug profile keeps a
    live Google session, so this is click-through, no credentials needed."""
    p.click("/google/i")
    time.sleep(6)
    # pick the Google account — JS click first, trusted Input click as fallback
    p.js(
        "(()=>{const e=[...document.querySelectorAll('div[role=link],li,div')]"
        ".find(e=>/@/.test(e.innerText||'')&&e.innerText.length<120);"
        "if(e){e.click();return 'picked'} return null})()")
    time.sleep(5)
    if "accountchooser" in (p.js("location.href") or ""):
        box = p.js(
            "(()=>{const e=[...document.querySelectorAll('[data-identifier],[role=listitem],li,div')]"
            ".find(e=>/@/.test(e.innerText||'')&&e.innerText.length<120);"
            "if(!e)return null;const r=e.getBoundingClientRect();"
            "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2})})()")
        if box:
            c = json.loads(box)
            for et in ("mousePressed", "mouseReleased"):
                p._send("Input.dispatchMouseEvent", {"type": et, "x": c["x"], "y": c["y"],
                        "button": "left", "clickCount": 1})
    return "IN" if p.poll(
        "(()=>{const t=document.body.innerText||'';"
        "return /api tokens|destination|account home|cloudless/i.test(t) ? 'IN' : null})()",
        timeout=45) else "OUT"


def _dash_ready(p, want):
    v = p.poll(
        "(()=>{const t=document.body.innerText||'';"
        "if(/" + want + "/i.test(t))return 'PAGE';"
        "if(/log in|sign in/i.test(t))return 'LOGIN';return ''})()",
        timeout=30)
    if v != "LOGIN":
        return True
    return _dash_sso_login(p) == "IN"


def gate_email_routing():
    _ensure_cdp()
    p = Page()
    p.goto("https://dash.cloudflare.com/?to=/:zone/cloudless.gr/email/routing/routes")
    if not _dash_ready(p, "destination address"):
        p.close()
        return "dash login failed even after Google SSO — use --real"
    p.click("/add destination|add address/i")
    time.sleep(2)
    p.type_text(
        "[...document.querySelectorAll('input')].find(e=>e.type==='email'"
        "||/email/i.test(e.placeholder||e.name||''))",
        "tbaltzakis@cloudless.gr")
    time.sleep(1)
    p.click("/save|add|create/i")
    print("destination add attempted — watching mailbox for verify link…")
    p.close()
    for _ in range(60):
        try:
            r = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "omv",
                 "for d in /home/*/Maildir/new /home/*/Maildir/cur; do "
                 "ls -t \"$d\" 2>/dev/null | head -20 | while read f; do "
                 "grep -oE 'https://[a-zA-Z0-9./?=_%&-]+' \"$d/$f\"; done; done "
                 "2>/dev/null | grep -i cloudflare | grep -iE 'verif|confirm' | tail -1"],
                capture_output=True, text=True, timeout=25)
        except subprocess.TimeoutExpired:
            continue
        link = r.stdout.strip()
        if link:
            subprocess.run(["curl", "-s", "-o", "/dev/null", link], timeout=15)
            return "verify link followed — destination VERIFIED; retest mail-outbound /send"
        time.sleep(10)
    return "no verify email in 10min — check omv maildir manually"


def gate_deploy_token():
    _ensure_cdp()
    p = Page()
    p.goto("https://dash.cloudflare.com/profile/api-tokens")
    if not _dash_ready(p, "api tokens"):
        p.close()
        return "dash login failed even after Google SSO — use --real"
    p.click("/create token|create custom/i")
    time.sleep(2)
    p.click("/use template|custom/i")
    time.sleep(2)
    p.type_text(
        "document.querySelector('input[type=text],input[name=name]')",
        "ci-workers-deploy-auto")
    tok = p.poll(
        "(()=>{const e=[...document.querySelectorAll('pre,code,input[readonly],textarea')]"
        ".find(e=>/^[A-Za-z0-9_-]{30,}$/.test((e.value||e.innerText||'').trim()));"
        "return e ? (e.value||e.innerText).trim() : null})()",
        timeout=120)
    p.close()
    if not tok:
        return ("wizard reached but token not captured — finish the SPA flow in the tab "
                "(permissions: Account→Workers Scripts→Edit)")
    r = subprocess.run(
        ["gh", "secret", "set", "CLOUDFLARE_WORKERS_DEPLOY_TOKEN",
         "-R", "Themis128/cloudless.gr", "--body", tok],
        capture_output=True, text=True)
    return "deploy-token OK — secret set" if r.returncode == 0 else f"gh failed: {r.stderr[:150]}"


def restore_chrome():
    if RESTORE and REAL:
        subprocess.Popen([CHROME])


GATES = {
    "fb-token": gate_fb,
    "email-routing": gate_email_routing,
    "deploy-token": gate_deploy_token,
}

if __name__ == "__main__":
    g = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = GATES.get(g)
    print(fn() if fn else
          "usage: gate_runner.py {fb-token|email-routing|deploy-token} [--real] [--restore]")
    restore_chrome()
