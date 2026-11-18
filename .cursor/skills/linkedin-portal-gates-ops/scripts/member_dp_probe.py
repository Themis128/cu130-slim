#!/usr/bin/env python3
"""member_dp_probe.py — probe/drive the LinkedIn developer-portal gates
(Member DP request-access modals, product states).

    member_dp_probe.py            # read-only state report
    member_dp_probe.py --json     # machine-readable
    member_dp_probe.py --drive    # best-effort: click Member DP (Member)
                                  # "Request access" and dump the modal
    member_dp_probe.py --url URL  # probe a different portal page
    member_dp_probe.py --via auto # sidecar first, CDP fallback (default)
    member_dp_probe.py --via sidecar|cdp   # force one lane

Lanes (default auto = sidecar, then CDP on transport failure or a
logged-out sidecar session):

- sidecar: linkedin-browser-sidecar /debug/navigate + /debug/eval over
  HTTP (LI_SIDECAR env, default http://localhost:9225). Durable session,
  no Windows CDP dependency; respects the sidecar rate-limit circuit.
- cdp: opens its OWN tab via /json/new on the Windows debug Chrome
  (never hijacks existing tabs), background-reader websocket, closes
  the tab when done. Endpoints tried: localhost:9229,
  " + WSL_HOST_IP + ":9229 (WSL->Windows relay), localhost:9222. Requires
  websocket-client.

Never submits forms — reports what it sees.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# WSL host IP — the Windows gateway moves subnets on vSwitch rebuilds
# (172.22.144.1 -> 192.168.96.1 observed 2026-11-18). Resolve live.
try:
    WSL_HOST_IP = os.environ.get("WSL_HOST_IP") or subprocess.run(
        ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
        capture_output=True, text=True, timeout=5).stdout.strip() \
        or "172.22.144.1"
except Exception:
    WSL_HOST_IP = "172.22.144.1"


CDP_CANDIDATES = ["http://localhost:9229", "http://" + WSL_HOST_IP + ":9229",
                  "http://localhost:9222"]
SIDECAR_BASE = os.environ.get("LI_SIDECAR", "http://localhost:9225")
STACKCTL = os.environ.get(
    "STACKCTL", os.path.expanduser("~/cu130-slim/scripts/stackctl.py"))
APP_URL = "https://www.linkedin.com/developers/apps/265077368"
APPS_URL = "https://www.linkedin.com/developers/apps"

EVAL_STATE = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  const onAuthWall = /login|checkpoint|authwall|session-expired/i.test(location.href)
    || !!document.querySelector('input[name="session_key"], #username, .login__form, #captcha');
  const loggedIn = !onAuthWall
    && !!document.body && document.body.innerText.length > 200;
  const rows = [];
  document.querySelectorAll('a,button,[role=button]').forEach(el => {
    const t = (el.textContent || '').trim();
    if (/request access|member dp|matched audience|community management|lead sync|pages dp|advertising api|events api/i.test(t) && t.length < 120)
      rows.push({text: t, tag: el.tagName, href: el.href || null});
  });
  return JSON.stringify({
    url: location.href, loggedIn,
    title: document.title,
    profileHint: (document.querySelector('.global-nav__me-photo') || {alt: null}).alt,
    rows: rows.slice(0, 40),
    snippet: text.slice(0, 600)
  });
})()
"""

EVAL_CLICK_MEMBER_DP = r"""
(() => {
  const els = [...document.querySelectorAll('a,button,[role=button],li,div')];
  const target = els.find(el => {
    const t = (el.textContent || '').trim();
    return /member dp/i.test(t) && t.length < 200;
  });
  if (!target) return JSON.stringify({clicked: false, why: 'no Member DP row'});
  const btn = [...target.querySelectorAll('a,button,[role=button]')]
    .find(b => /request access/i.test(b.textContent || ''))
    || [...document.querySelectorAll('a,button,[role=button]')]
       .find(b => /request access/i.test(b.textContent || '')
         && target.contains(b));
  if (!btn) return JSON.stringify({clicked: false, why: 'no request-access btn near Member DP'});
  btn.click();
  return JSON.stringify({clicked: true, btn: (btn.textContent||'').trim().slice(0,80)});
})()
"""

# Sidecar lane: /debug/eval wraps `expr` as `(async () => { <expr> })()`,
# so these are statement bodies ending in `return JSON.stringify(...)` —
# same payloads as the CDP IIFEs above.
EVAL_STATE_SC = r"""
const text = document.body ? document.body.innerText : '';
const onAuthWall = /login|checkpoint|authwall|session-expired/i.test(location.href)
  || !!document.querySelector('input[name="session_key"], #username, .login__form, #captcha');
const loggedIn = !onAuthWall
  && !!document.body && document.body.innerText.length > 200;
const rows = [];
document.querySelectorAll('a,button,[role=button]').forEach(el => {
  const t = (el.textContent || '').trim();
  if (/request access|member dp|matched audience|community management|lead sync|pages dp|advertising api|events api/i.test(t) && t.length < 120)
    rows.push({text: t, tag: el.tagName, href: el.href || null});
});
return JSON.stringify({
  url: location.href, loggedIn,
  title: document.title,
  profileHint: (document.querySelector('.global-nav__me-photo') || {alt: null}).alt,
  rows: rows.slice(0, 40),
  snippet: text.slice(0, 600)
});
"""

EVAL_CLICK_SC = r"""
const els = [...document.querySelectorAll('a,button,[role=button],li,div')];
const target = els.find(el => {
  const t = (el.textContent || '').trim();
  return /member dp/i.test(t) && t.length < 200;
});
if (!target) return JSON.stringify({clicked: false, why: 'no Member DP row'});
const btn = [...target.querySelectorAll('a,button,[role=button]')]
  .find(b => /request access/i.test(b.textContent || ''))
  || [...document.querySelectorAll('a,button,[role=button]')]
     .find(b => /request access/i.test(b.textContent || '')
       && target.contains(b));
if (!btn) return JSON.stringify({clicked: false, why: 'no request-access btn near Member DP'});
btn.click();
return JSON.stringify({clicked: true, btn: (btn.textContent||'').trim().slice(0,80)});
"""


def _sc_req(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        SIDECAR_BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _sc_eval(expr):
    r = _sc_req("POST", "/debug/eval", {"expr": expr}, timeout=45)
    val = r.get("value")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            pass
    return val, r


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


def _open_tab(base, url):
    req = urllib.request.Request(
        base + "/json/new?" + urllib.parse.quote(url, safe=""),
        method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _close_tab(base, tab_id):
    try:
        urllib.request.urlopen(base + "/json/close/" + tab_id, timeout=5)
    except Exception:
        pass


class CDP:
    def __init__(self, ws_url):
        import threading
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=30,
                                              suppress_origin=True)
        self._responses = {}
        self._cv = threading.Condition()
        self._dead = None
        self._mid = 0
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self):
        try:
            while True:
                msg = json.loads(self.ws.recv())
                mid = msg.get("id")
                if mid is not None:
                    with self._cv:
                        self._responses[mid] = msg.get("result", {})
                        self._cv.notify_all()
        except Exception as e:
            self._dead = e
            with self._cv:
                self._cv.notify_all()

    def cmd(self, method, params=None, timeout=45):
        self._mid += 1
        mid = self._mid
        try:
            self.ws.send(json.dumps({"id": mid, "method": method,
                                     "params": params or {}}))
        except Exception as e:
            return {"_error": str(e)}
        deadline = time.time() + timeout
        with self._cv:
            while mid not in self._responses:
                if self._dead:
                    return {"_error": f"ws closed: {self._dead}"}
                remaining = deadline - time.time()
                if remaining <= 0:
                    return {"_error": "timeout"}
                self._cv.wait(remaining)
            return self._responses.pop(mid)

    def eval(self, expr):
        r = self.cmd("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": True})
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def probe_sidecar(drive=False, url=None):
    h = _sc_req("GET", "/health", timeout=15)
    if h.get("error") and os.path.exists(STACKCTL):
        # sidecar is stack-ops sleep-managed — wake it once, then re-probe
        try:
            subprocess.run(
                [sys.executable, STACKCTL, "wake",
                 "linkedin-browser-sidecar"],
                timeout=150, capture_output=True)
        except Exception:
            pass
        h = _sc_req("GET", "/health", timeout=15)
    if h.get("error"):
        return {"status": "no-sidecar",
                "detail": f"sidecar unreachable at {SIDECAR_BASE}: "
                          f"{h['error']}"}
    if h.get("rate_limited") or h.get("circuit_open"):
        return {"status": "sidecar-rate-limited",
                "detail": "sidecar rate-limited/circuit-open — "
                          "retry after cooldown"}
    nav = _sc_req("POST", "/debug/navigate",
                  {"url": url or APP_URL}, timeout=60)
    if nav.get("error"):
        return {"status": "navigate-error",
                "detail": str(nav["error"])[:200]}
    time.sleep(9)
    state, r = _sc_eval(EVAL_STATE_SC)
    if not isinstance(state, dict):
        return {"status": "eval-error",
                "detail": str(r.get("error") or state)[:200]}
    state["via"] = "sidecar"
    state["drive"] = None
    if drive and state.get("loggedIn"):
        time.sleep(2)
        dval, _ = _sc_eval(EVAL_CLICK_SC)
        state["drive"] = dval
        time.sleep(3)
        after, _ = _sc_eval(EVAL_STATE_SC)
        state["after"] = after
    return state


def probe(drive=False, url=None, via="auto"):
    if via == "sidecar":
        return probe_sidecar(drive=drive, url=url)
    if via == "auto":
        res = probe_sidecar(drive=drive, url=url)
        failed = res.get("status") in (
            "no-sidecar", "navigate-error", "eval-error",
            "sidecar-rate-limited") or res.get("loggedIn") is False
        if failed:
            cdp = probe(drive=drive, url=url, via="cdp")
            if cdp.get("status") != "no-cdp":
                cdp["sidecar_fallback"] = res.get("status") or "logged-out"
                return cdp
        return res
    base = _cdp_base()
    if not base:
        return {"status": "no-cdp",
                "detail": "no CDP endpoint reachable "
                          "(tried " + ", ".join(CDP_CANDIDATES) + ")"}
    tab = _open_tab(base, url or APP_URL)
    tab_id = tab.get("id", "")
    time.sleep(9)
    c = CDP(tab["webSocketDebuggerUrl"])
    try:
        raw = c.eval(EVAL_STATE)
        state = json.loads(raw) if raw else {"status": "eval-failed"}
        state["cdp"] = base
        state["drive"] = None
        if drive and state.get("loggedIn"):
            time.sleep(2)
            raw2 = c.eval(EVAL_CLICK_MEMBER_DP)
            state["drive"] = json.loads(raw2) if raw2 else {"clicked": False}
            time.sleep(3)
            raw3 = c.eval(EVAL_STATE)
            state["after"] = json.loads(raw3) if raw3 else None
        return state
    finally:
        c.close()
        _close_tab(base, tab_id)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    drive = "--drive" in sys.argv
    via = "auto"
    if "--via" in sys.argv:
        i = sys.argv.index("--via")
        via = sys.argv[i + 1] if i + 1 < len(sys.argv) else "auto"
    url = APP_URL
    if "--url" in sys.argv:
        i = sys.argv.index("--url")
        url = sys.argv[i + 1]
    res = probe(drive=drive, url=url, via=via)
    if "--json" in sys.argv:
        print(json.dumps(res, indent=1))
        return 0
    lane = res.get("via") or ("cdp" if res.get("cdp") else None)
    print(f"lane: {lane or res.get('status')}")
    if res.get("status") in ("no-cdp", "no-sidecar", "navigate-error",
                             "eval-error", "sidecar-rate-limited"):
        print(res.get("detail"))
        return 2
    print(f"url: {res.get('url')}")
    print(f"loggedIn: {res.get('loggedIn')}  "
          f"profile: {res.get('profileHint')}")
    print("rows:")
    for r in res.get("rows", []):
        print(f"  - {r['text'][:90]}"
              + (f"  -> {r['href']}" if r.get("href") else ""))
    if res.get("snippet"):
        print("snippet:", res["snippet"][:200].replace("\n", " "))
    if res.get("drive"):
        print("drive:", json.dumps(res["drive"]))
        if res.get("after"):
            print("after snippet:",
                  (res["after"].get("snippet") or "")[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
