#!/usr/bin/env python3
"""member_dp_probe.py — probe/drive the LinkedIn developer-portal gates
(Member DP request-access modals, product states) over CDP.

    member_dp_probe.py            # read-only state report
    member_dp_probe.py --json     # machine-readable
    member_dp_probe.py --drive    # best-effort: click Member DP (Member)
                                  # "Request access" and dump the modal
    member_dp_probe.py --url URL  # probe a different portal page

Opens its OWN tab via /json/new (never hijacks existing tabs), uses a
background-reader websocket (Qualtrics/LinkedIn flood events and the
relay kills undrained sockets), and closes the tab when done.

CDP endpoints tried: localhost:9229, 172.22.144.1:9229 (WSL->Windows
relay), localhost:9222 (direct debug Chrome). Requires websocket-client.
Never submits forms — reports what it sees.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

CDP_CANDIDATES = ["http://localhost:9229", "http://172.22.144.1:9229",
                  "http://localhost:9222"]
APP_URL = "https://www.linkedin.com/developers/apps/265077368"
APPS_URL = "https://www.linkedin.com/developers/apps"

EVAL_STATE = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  const loggedIn = !!document.querySelector('img.global-nav__me-photo, '
    + 'button.global-nav__primary-link-me, [data-control-name="nav.settings"]');
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


def probe(drive=False, url=None):
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
    url = APP_URL
    if "--url" in sys.argv:
        i = sys.argv.index("--url")
        url = sys.argv[i + 1]
    res = probe(drive=drive, url=url)
    if "--json" in sys.argv:
        print(json.dumps(res, indent=1))
        return 0
    print(f"cdp: {res.get('cdp', res.get('status'))}")
    if res.get("status") == "no-cdp":
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
