#!/usr/bin/env python3
"""Drive a Gmail OAuth consent through the CDP debug Chrome — no human click
when the Google session in that profile covers the target account.

  python3 gmail_consent_drive.py themis.baltzakis@gmail.com [--scope gmail.modify]

Reuses gmail.py's client-secret loader + env writer. Binds its own loopback
listener on 127.0.0.1:50000 (kills a stale one first), builds the consent URL,
navigates a tab on the :9229 CDP relay, clicks through account-chooser /
consent screens, and verifies the refresh token lands in .env.

Exits 0 when the token exists (idempotent — safe for cron). Exits 3 when a
human gate remains (password / 2FA / account not signed in).
"""
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail  # noqa: E402  (client secret loader, _post, _save_env, _env)

CDP = "192.168.96.1:9229"
SCOPE = "https://www.googleapis.com/auth/gmail.modify"  # read + trash/modify
DEFAULT_SCOPE_ARG = "gmail.modify"

_state = {}


def _env_key(account: str) -> str:
    return "GMAIL_REFRESH_TOKEN_" + account.upper().replace(".", "_").replace("@", "_")


def _jget(path, tries=5):
    for _ in range(tries):
        try:
            return json.load(urllib.request.urlopen(f"http://{CDP}{path}", timeout=6))
        except Exception:
            time.sleep(1.5)
    raise RuntimeError("cdp http unreachable")


def _connect():
    import websocket
    ver = _jget("/json/version")
    return websocket.create_connection(
        ver["webSocketDebuggerUrl"], suppress_origin=True, timeout=20), [0]


def _rpc(ws, mid, method, params=None, session=None):
    mid[0] += 1
    msg = {"id": mid[0], "method": method, "params": params or {}}
    if session:
        msg["sessionId"] = session
    ws.send(json.dumps(msg))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid[0]:
            return m.get("result", {})


def ensure_tab():
    """(Re)connect and return (ws, mid, sessionId) on our consent tab."""
    if _state.get("session"):
        try:
            ws, mid = _state["ws"], _state["mid"]
            _rpc(ws, mid, "Runtime.evaluate", {"expression": "1"}, _state["session"])
            return ws, mid, _state["session"]
        except Exception:
            _state.clear()
    ws, mid = _connect()
    targets = _jget("/json")
    page = next((t for t in targets if t.get("type") == "page"
                 and ("accounts.google" in t.get("url", "")
                      or "localhost:50000" in t.get("url", ""))), None)
    tid = page["id"] if page else _rpc(
        ws, mid, "Target.createTarget", {"url": "about:blank"})["targetId"]
    sid = _rpc(ws, mid, "Target.attachToTarget",
               {"targetId": tid, "flatten": True})["sessionId"]
    _state.update(ws=ws, mid=mid, session=sid)
    return ws, mid, sid


def ev(expr):
    for _ in range(3):
        try:
            ws, mid, sid = ensure_tab()
            r = _rpc(ws, mid, "Runtime.evaluate",
                     {"expression": expr, "returnByValue": True,
                      "awaitPromise": True}, sid)
            return r.get("result", {}).get("value")
        except Exception:
            _state.clear()
            time.sleep(2)
    return None


def nav(url):
    for _ in range(4):
        try:
            ws, mid, sid = ensure_tab()
            _rpc(ws, mid, "Page.enable", session=sid)
            _rpc(ws, mid, "Page.navigate", {"url": url}, sid)
            return
        except Exception:
            _state.clear()
            time.sleep(2)
    raise RuntimeError("nav failed: cdp unreachable")


def _trusted_click_text(*needles):
    """Click first visible element whose innerText matches — via real mouse
    events at the element's center (synthetic el.click() is ignored by
    Google's Angular pages)."""
    js = """(() => {
      const needles = %s;
      const groups = [
        [...document.querySelectorAll('button, [role="button"]')],
        [...document.querySelectorAll('li, div[role="link"], a, span')]
      ];
      for (const els of groups) {
        for (const el of els) {
          const t = (el.innerText || '').trim();
          if (!t || t.length > 60) continue;
          for (const n of needles) {
            if (t.toLowerCase().includes(n.toLowerCase())) {
              const r = el.getBoundingClientRect();
              if (r.width < 5 || r.height < 5) continue;
              return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2, text: t.slice(0,40)});
            }
          }
        }
      }
      return null;
    })()""" % json.dumps([n.lower() for n in needles])
    hit = ev(js)
    if not hit:
        return None
    r = json.loads(hit)
    ws, mid, sid = ensure_tab()
    for evt in ("mousePressed", "mouseReleased"):
        _rpc(ws, mid, "Input.dispatchMouseEvent",
             {"type": evt, "x": r["x"], "y": r["y"],
              "button": "left", "clickCount": 1}, sid)
    return "clicked:" + r["text"]


def click_text(*needles):
    return _trusted_click_text(*needles)


def click_account(email: str):
    js = """(() => {
      const email = %s.toLowerCase();
      const els = [...document.querySelectorAll('li, div[role="link"], div[role="button"], a')];
      for (const el of els) {
        const t = (el.innerText || '').toLowerCase();
        if (t.includes(email)) {
          const r = el.getBoundingClientRect();
          if (r.width < 5) continue;
          return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
        }
      }
      return null;
    })()""" % json.dumps(email)
    hit = ev(js)
    if not hit:
        return None
    r = json.loads(hit)
    ws, mid, sid = ensure_tab()
    for evt in ("mousePressed", "mouseReleased"):
        _rpc(ws, mid, "Input.dispatchMouseEvent",
             {"type": evt, "x": r["x"], "y": r["y"],
              "button": "left", "clickCount": 1}, sid)
    return "acct-clicked"


def _type_into(selector: str, text: str):
    """Focus `selector` and type via CDP Input.insertText (real key events)."""
    ws, mid, sid = ensure_tab()
    focus = f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return false;
      el.focus(); el.click(); return true;
    }})()"""
    r = _rpc(ws, mid, "Runtime.evaluate",
             {"expression": focus, "returnByValue": True}, sid)
    if not r.get("result", {}).get("value"):
        return False
    _rpc(ws, mid, "Input.insertText", {"text": text}, sid)
    return True


def _press_enter():
    ws, mid, sid = ensure_tab()
    for evt in ("keyDown", "keyUp"):
        _rpc(ws, mid, "Input.dispatchKeyEvent",
             {"type": evt, "key": "Enter", "code": "Enter",
              "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}, sid)


def signin_with_stored_password(account: str) -> str:
    """Attempt 'Use another account' -> email -> stored Google password.

    Returns 'ok' | 'wrong-password' | 'human-gate' | 'no-password'.
    Single password attempt only — repeated failures lock the account.
    """
    pw = gmail._env("GOOGLE_ACCOUNT_PASSWORD") or gmail._env("GOOGLE_PASSWORD")
    if not pw:
        return "no-password"
    # chooser -> 'Use another account' (trusted click — el.click() is ignored)
    r = _trusted_click_text("use another account")
    if not r:
        return "human-gate"
    time.sleep(4)
    if not _type_into('input[name="identifier"], input[type="email"]', account):
        return "human-gate"
    time.sleep(1)
    _trusted_click_text("next") or _press_enter()
    time.sleep(5)
    # Google's pwd challenge renders a hidden decoy `hiddenPassword` input
    # BEFORE the real `Passwd` field — must target Passwd explicitly.
    if not _type_into('input[name="Passwd"], input[type="password"]:not([name="hiddenPassword"])', pw):
        body = (ev("document.body ? document.body.innerText.slice(0,300) : ''") or "").lower()
        return "wrong-password" if "wrong" in body or "incorrect" in body else "human-gate"
    time.sleep(1)
    _trusted_click_text("next") or _press_enter()
    # Poll for resolution — the pwd challenge redirects slowly, and the
    # continue= URL param contains the word "consent" even on challenge pages,
    # so URL substring checks alone are unreliable.
    deadline2 = time.time() + 40
    while time.time() < deadline2:
        cur = ev("location.href") or ""
        body = (ev("document.body ? document.body.innerText.slice(0,400) : ''") or "").lower()
        if "localhost:50000" in cur:
            return "ok"
        if "/challenge/" in cur or "challenge/pwd" in cur:
            if any(k in body for k in ("wrong password", "incorrect password",
                                       "couldn't sign you in", "try again")):
                return "wrong-password"
            if any(k in body for k in ("2-step", "verify", "phone", "authenticator",
                                       "tap yes", "check your")):
                return "human-gate"
            time.sleep(3)
            continue
        if "/signin/oauth/" in cur or "consent" in cur.split("?")[0]:
            return "ok"
        time.sleep(3)
    return "human-gate"


class _H(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        if self.path.startswith("/auth/callback"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            _H.code = q.get("code", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h3>Access granted.</h3>")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def kill_stale_listener():
    """Free :50000 if a previous consent listener is still bound."""
    try:
        out = __import__("subprocess").run(
            ["ss", "-tlnp"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if ":50000" in line and "pid=" in line:
                pid = re.search(r"pid=(\d+)", line).group(1)
                __import__("os").kill(int(pid), 15)
                time.sleep(1)
                return True
    except Exception:
        pass
    return False


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else ""
    if not account:
        raise SystemExit(__doc__)
    scope = SCOPE
    if "--scope" in sys.argv:
        scope = "https://www.googleapis.com/auth/" + sys.argv[sys.argv.index("--scope") + 1]
    key = _env_key(account)
    if gmail._env(key):
        print(f"token already live: {key}")
        return 0

    kill_stale_listener()
    srv = HTTPServer(("127.0.0.1", 50000), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    cs = gmail._load_secret()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cs["client_id"],
        "redirect_uri": "http://localhost:50000/auth/callback",
        "response_type": "code", "scope": scope,
        "access_type": "offline", "prompt": "consent"})
    print(f"[drive] consent URL minted for {account} scope={scope.split('/')[-1]}", flush=True)

    deadline = time.time() + 600
    nav(url)
    last_state = ""
    signin_attempted = False
    while time.time() < deadline:
        if _H.code:
            break
        cur = ev("location.href") or ""
        body = (ev("document.body ? document.body.innerText.slice(0,300) : ''") or "")
        state = f"{cur[:60]} | {body[:80].replace(chr(10),' ')}"
        if state != last_state:
            print(f"[drive] {state}", flush=True)
            last_state = state
        low = body.lower()
        if "localhost:50000" in cur:
            time.sleep(2)
            continue
        # account chooser
        if "choose an account" in low or "select an account" in low:
            r = click_account(account)
            print(f"[drive] chooser -> {r}", flush=True)
            if not r and not signin_attempted:
                signin_attempted = True
                res = signin_with_stored_password(account)
                print(f"[drive] signin attempt -> {res}", flush=True)
                if res == "wrong-password":
                    print("[drive] HUMAN-GATE: stored Google password rejected", flush=True)
                    return 3
                if res == "human-gate":
                    print("[drive] HUMAN-GATE: sign-in needs credentials/2FA", flush=True)
                    return 3
                if res == "no-password":
                    print("[drive] HUMAN-GATE: no stored Google password", flush=True)
                    return 3
        # OAuth consent screen — NOT challenge/signin pages ("Sign in with
        # Google" appears on every Google page, so it can't be a needle).
        elif ("/challenge/" not in cur and "accountchooser" not in cur
              and "identifier" not in cur
              and any(k in low for k in ("wants to access", "continue", "allow"))):
            r = click_text("continue", "allow", "next", "approve", "confirm")
            if r:
                print(f"[drive] consent -> {r}", flush=True)
        # hard human gates
        elif any(k in low for k in ("enter your password", "verify it", "2-step", "phone number")):
            print("[drive] HUMAN-GATE: credentials/2FA required — cannot complete unattended", flush=True)
            return 3
        time.sleep(5)

    if not _H.code:
        print("[drive] TIMEOUT waiting for consent", flush=True)
        return 4
    tok = gmail._post("https://oauth2.googleapis.com/token", {
        "code": _H.code, "client_id": cs["client_id"],
        "client_secret": cs["client_secret"],
        "redirect_uri": "http://localhost:50000/auth/callback",
        "grant_type": "authorization_code"})
    gmail._save_env(key, tok["refresh_token"])
    print(f"[drive] saved {key} to .env", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
