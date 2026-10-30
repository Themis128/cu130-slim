#!/usr/bin/env python3
"""obs_cancel — cancel the Cloudflare Observability subscription via the
operator's REAL logged-in dashboard session (CDP). Billing-plane has no
API scope, so this drives the UI — same pattern as consent-root-ops.

Prereq: Chrome debug port up (start-chrome-debug.bat) and the operator's
browser logged into dash.cloudflare.com.

    obs_cancel.py run       # navigate → cancel → verify
    obs_cancel.py check     # just report the subscription row state
"""
import json
import sys
import time
import urllib.request

CDP = "http://localhost:9222"
ACC = "fb7dc7b69b662480cd5961a4d1913c78"
BILLING = f"https://dash.cloudflare.com/{ACC}/billing/subscriptions"


def _ws_eval(tab_url_prefix: str, expr: str, timeout: int = 15) -> str:
    import websocket  # websocket-client
    for t in json.loads(urllib.request.urlopen(CDP + "/json").read()):
        if t.get("url", "").startswith(tab_url_prefix):
            ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=timeout)
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True}}))
            out = json.loads(ws.recv())["result"]["result"].get("value")
            ws.close()
            return out
    return ""


def check() -> int:
    txt = _ws_eval("https://dash.cloudflare.com", "document.body.innerText.slice(0,4000)")
    if not txt:
        print("no dashboard tab — open dash.cloudflare.com in the debug browser")
        return 2
    print("observability rows:",
          [l for l in txt.splitlines() if "bservability" in l][:3] or "none visible")
    return 0


def run() -> int:
    # navigate to subscriptions
    target = json.loads(urllib.request.urlopen(CDP + "/json").read())
    tab = next((t for t in target if "dash.cloudflare.com" in t.get("url", "")), None)
    if not tab:
        print("no CF dashboard tab found")
        return 2
    import websocket
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20)
    def _call(mid, method, params=None):
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid:
                return m.get("result", {})
    _call(1, "Page.enable")
    _call(2, "Page.navigate", {"url": BILLING})
    time.sleep(8)
    _call(3, "Runtime.evaluate", {"expression": "document.body.innerText.slice(0,6000)",
                                  "returnByValue": True})
    body = _call(3, "Runtime.evaluate", {})["result"]["result"].get("value", "")
    # find + click the Observability cancel control
    js = """(()=>{
      const rows=[...document.querySelectorAll('*')].filter(e=>
        /observability/i.test(e.textContent||'') && e.children.length<8)
      if(!rows.length) return 'no-row';
      const btn=[...document.querySelectorAll('button,a')].find(b=>
        /cancel|downgrade|free plan/i.test(b.textContent||''))
      if(!btn) return 'no-cancel-btn';
      btn.click()
 return 'clicked'
    })()"""
    r = _call(4, "Runtime.evaluate",
              {"expression": js, "returnByValue": True})["result"]["result"].get("value")
    print("row click:", r)
    ws.close()
    return 0 if r == "clicked" else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit({"run": run, "check": check}[cmd]())
