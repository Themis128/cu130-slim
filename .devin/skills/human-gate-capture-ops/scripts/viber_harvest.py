#!/usr/bin/env python3
"""viber_harvest — pull the bot auth token from webadmin.viber.com via the
operator's logged-in browser session (CDP), then wire the account.

    viber_harvest.py run     # navigate partners → extract token → provision
    viber_harvest.py check   # is the partners session live?
"""
import json
import sys
import urllib.request

CDP = "http://localhost:9222"
SKILLS = "/home/tbaltzakis/cu130-slim/.devin/skills"


def _tab(host_frag: str):
    try:
        tabs = json.loads(urllib.request.urlopen(CDP + "/json", timeout=8).read())
    except Exception:
        print("debug browser down — start-chrome-debug.bat")
        return []
    for t in tabs if isinstance(tabs,list) else []:
        if host_frag in t.get("url", ""):
            return t
    return None


def check() -> int:
    t = _tab("webadmin.viber.com")
    print("partners tab:", "found" if t else "absent — open webadmin.viber.com logged in")
    return 0 if t else 2


def run() -> int:
    t = _tab("webadmin.viber.com")
    if not t:
        print("open webadmin.viber.com (logged in) in the debug browser first")
        return 2
    import websocket
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=20)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
        "expression": """(()=>{
          const tok=[...document.querySelectorAll('code,pre,input,td')].map(e=>e.value||e.textContent)
            .map(s=>String(s).trim()).find(s=>/^[a-f0-9-]{30,}$/i.test(s))
          const names=[...document.querySelectorAll('*')].filter(e=>/token/i.test(e.textContent||'')&&e.children.length<3).length
          return JSON.stringify({tok:tok||'',tokenish:names,url:location.href})
        })()""", "returnByValue": True}}))
    out = json.loads(ws.recv())["result"]["result"].get("value")
    ws.close()
    d = json.loads(out or "{}")
    tok = d.get("tok", "")
    if not tok:
        print("no token visible — navigate to the bot's info page first")
        return 1
    import subprocess
    r = subprocess.run(
        ["python3", f"{SKILLS}/viber-ops/scripts/viber_provision.py",
         "--token", tok], capture_output=True, text=True, timeout=60)
    print("provision:", "ok" if r.returncode == 0 else r.stderr[:150])
    return r.returncode


if __name__ == "__main__":
    sys.exit({"run": run, "check": check}[sys.argv[1] if len(sys.argv) > 1 else "check"]())
