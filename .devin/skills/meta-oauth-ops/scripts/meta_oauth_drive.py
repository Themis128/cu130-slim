#!/usr/bin/env python3
"""Drive a SocialAuto OAuth authorize URL through Chrome CDP to completion.

Usage: python meta_oauth_drive.py <authorize_url> [--cdp http://localhost:9224]

Handles: FB/IG login forms (creds via FB_EMAIL/FB_PASS env), consent screens,
checkpoint/2FA pause (reports state, waits for human), success detection via
redirect to social.cloudless.gr callback.
"""
import json
import os
import sys
import time
import urllib.request

CDP = "http://localhost:9330"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PROFILE = r"C:\Users\tbaltzakis\chrome-debug-profile"
EMAIL = os.environ.get("FB_EMAIL", "")
PASSWORD = os.environ.get("FB_PASS", "")

_id = 0


def tabs(port=9330):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json", timeout=5) as f:
            return [t for t in json.loads(f.read()) if t.get("type") == "page"]
    except Exception:
        return []


def ensure_cdp():
    global CDP
    if tabs():
        return
    import subprocess
    subprocess.Popen([CHROME, "--remote-debugging-port=9330",
                      f"--user-data-dir={DEBUG_PROFILE}",
                      "--no-first-run", "--no-default-browser-check", "about:blank"])
    for _ in range(30):
        if tabs():
            CDP = "http://localhost:9330"
            return
        time.sleep(1)
    raise SystemExit("CDP never came up")


class Page:
    def __init__(self, url_match=None):
        import websocket
        ts = tabs()
        if url_match:
            ts = [t for t in ts if url_match in t.get("url", "")] or ts
        self.ws = websocket.create_connection(ts[0]["webSocketDebuggerUrl"],
                                              timeout=30, suppress_origin=True)
        for m in ("Page.enable", "Runtime.enable"):
            self._send(m)

    def _send(self, method, params=None):
        global _id
        _id += 1
        mid = _id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == mid:
                return r

    def goto(self, url, settle=4):
        self._send("Page.navigate", {"url": url})
        time.sleep(settle)

    def url(self):
        ts = tabs()
        if not ts:
            return ""
        # re-evaluate location directly
        return self.js("location.href") or ""

    def js(self, expr):
        r = self._send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True})
        res = (r.get("result") or {})
        if res.get("exceptionDetails"):
            return None
        return (res.get("result") or {}).get("value")

    def poll(self, expr, timeout=60, interval=1.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = self.js(expr)
            if v:
                return v
            time.sleep(interval)
        return None

    def type_into(self, selector_js, value):
        return self.js(
            "(()=>{const i=(" + selector_js + ");if(!i)return 'NOINPUT';"
            "i.focus();const s=Object.getOwnPropertyDescriptor("
            "HTMLInputElement.prototype,'value').set;"
            "s.call(i," + json.dumps(value) + ");"
            "i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.dispatchEvent(new Event('change',{bubbles:true}));return 'TYPED'})()")

    def click_text(self, texts):
        arr = json.dumps(texts)
        return self.js(
            "(()=>{const want=" + arr + ";"
            "const els=[...document.querySelectorAll('button,a,div[role=button],input[type=submit]')];"
            "for(const w of want){const b=els.find(e=>{const t=((e.innerText||e.value||"
            "e.getAttribute('aria-label')||'')+'').trim().toLowerCase();"
            "return t===w.toLowerCase()||t.includes(w.toLowerCase())});"
            "if(b){b.click();return 'CLICKED:'+w}}return null})()")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# State classifier — evaluated in page, returns a short state string or ''.
STATE_JS = r"""
(()=>{
  const u=location.href, b=document.body?document.body.innerText.slice(0,4000):'';
  if(/social\.cloudless\.gr\/api\/v1\/auth\/oauth\/[^/]+\/callback/.test(u)) return 'CALLBACK:'+u;
  if(/access_token=|code=/.test(u)&&/facebook\.com|threads\.net/.test(u)===false) return 'REDIRECT:'+u;
  const t=(document.title+' '+b).toLowerCase();
  // 2FA / checkpoint gates — need human
  if(/approve.*(login|sign)|check your notifications|enter the code|confirmation code|two-factor|verify your identity|suspicious|unusual activity/.test(t)) return 'GATE_2FA';
  // login forms
  const em=document.querySelector('input[name=email],input[type=email],input#email');
  const pw=document.querySelector('input[name=pass],input[type=password],input#pass');
  if(em&&pw) return 'LOGIN_FORM';
  const un=document.querySelector('input[name=username],input#username');
  if(un&&pw) return 'LOGIN_FORM_IG';
  // consent / continue buttons
  const btns=[...document.querySelectorAll('button,a,div[role=button],input[type=submit]')]
    .map(e=>((e.innerText||e.value||e.getAttribute('aria-label')||'')+'').trim());
  if(btns.some(x=>/continue as|allow|continue|log in with|authorize|got it|next/i.test(x))
     && /facebook|threads|instagram|meta/i.test(u)) return 'CONSENT';
  return '';
})()
"""


def login_fb(p):
    """Fill + submit the FB/IG login form once."""
    if not EMAIL or not PASSWORD:
        return "no creds in env"
    r = p.type_into(
        "document.querySelector('input[name=email],input[type=email],input#email,"
        "input[name=username],input#username')", EMAIL)
    if r != "TYPED":
        return f"email field: {r}"
    r = p.type_into(
        "document.querySelector('input[name=pass],input[type=password],input#pass')", PASSWORD)
    if r != "TYPED":
        return f"pass field: {r}"
    time.sleep(0.6)
    c = p.click_text(["Log in", "Log In", "Sign in", "Continue", "Next"])
    if not c:
        # submit the form directly
        p.js("(()=>{const f=document.querySelector('form');if(f){f.submit();return 'SUBMIT'}"
             "const b=document.querySelector('button[type=submit]');if(b){b.click();return 'CLICK'}"
             "return 'NOFORM'})()")
    return f"submitted ({c})"


def main():
    url = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    if not url:
        return "usage: meta_oauth_drive.py <authorize_url>"
    ensure_cdp()
    p = Page()
    p.goto(url, settle=5)
    deadline = time.time() + 300
    logged_in = False
    last_state = ""
    while time.time() < deadline:
        st = p.poll(STATE_JS, timeout=8, interval=2) or ""
        if st != last_state:
            print(f"[{time.strftime('%H:%M:%S')}] state={st or 'blank'} url={p.url()[:90]}", flush=True)
            last_state = st
        if st.startswith("CALLBACK"):
            print("SUCCESS callback reached:", st.split(":", 1)[1][:120])
            return "OK"
        if st == "GATE_2FA":
            print("HUMAN GATE: 2FA/checkpoint — approve on phone or complete in browser")
            # keep polling; user may approve on phone
            time.sleep(10)
            continue
        if st in ("LOGIN_FORM", "LOGIN_FORM_IG") and not logged_in:
            res = login_fb(p)
            print("login attempt:", res, flush=True)
            logged_in = True
            time.sleep(6)
            continue
        if st == "CONSENT":
            c = p.click_text(["Continue as", "Continue", "Allow", "Got it", "Next"])
            print("consent click:", c, flush=True)
            time.sleep(4)
            continue
        time.sleep(2)
    print("TIMEOUT — final url:", p.url()[:150])
    return "TIMEOUT"


if __name__ == "__main__":
    print(main())
