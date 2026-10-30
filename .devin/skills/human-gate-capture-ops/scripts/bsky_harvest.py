#!/usr/bin/env python3
"""bsky_harvest — pull a live Bluesky session from the debug browser,
mint an app password via XRPC, and wire the account.

Needs: Chrome :9222 (start-chrome-debug.bat — now in Startup) with a
logged-in bsky.app tab/session.

    bsky_harvest.py run      # extract session → app password → connect
    bsky_harvest.py check    # is a live session present?
"""
import json
import sys
import urllib.request

CDP = "http://localhost:9222"
SKILLS = "/home/tbaltzakis/cu130-slim/.devin/skills"


def _eval_tabs(expr: str) -> str:
    import websocket
    try:
        tabs = json.loads(urllib.request.urlopen(CDP + "/json", timeout=8).read())
    except Exception:
        print("debug browser down — start-chrome-debug.bat"); return []
    for t in tabs if isinstance(tabs,list) else []:
        if "bsky.app" not in t.get("url", ""):
            continue
        ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=15)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr,
                                       "returnByValue": True}}))
        out = json.loads(ws.recv())["result"]["result"].get("value")
        ws.close()
        return out or ""
    return ""


def check() -> int:
    js = """(()=>{const ks=Object.keys(localStorage);
      const sk=ks.find(k=>k.includes('session'));
      if(!sk) return 'no-session';
      try{const s=JSON.parse(localStorage.getItem(sk));
        const accts=s.accounts||s;
        const h=Object.values(accts).find(a=>a&&(a.refreshJwt||a.accessJwt));
        return h?'live:'+(h.handle||'?'):'no-session'}catch(e){return 'parse:'+e}
    })()"""
    r = _eval_tabs(js) or "no-bsky-tab"
    print(r)
    return 0 if r.startswith("live:") else 2


def run() -> int:
    if check() != 0:
        print("no live bsky session in debug browser — log into bsky.app first")
        return 2
    js = """(()=>{const ks=Object.keys(localStorage);
      const sk=ks.find(k=>k.includes('session'));
      const s=JSON.parse(localStorage.getItem(sk));
      const accts=s.accounts||s;
      const a=Object.values(accts).find(a=>a&&(a.refreshJwt||a.accessJwt));
      return JSON.stringify({handle:a.handle,refresh:a.refreshJwt||'',svc:a.service||'https://bsky.social'});
    })()"""
    raw = _eval_tabs(js)
    sess = json.loads(raw)
    # mint app password via createSession(refresh) → createAppPassword
    import requests
    s2 = requests.post(f"{sess['svc']}/xrpc/com.atproto.server.refreshSession",
                       headers={"Authorization": f"Bearer {sess['refresh']}"},
                       timeout=20).json()
    tok = s2.get("accessJwt", "")
    pw = requests.post(f"{sess['svc']}/xrpc/com.atproto.server.createAppPassword",
                       headers={"Authorization": f"Bearer {tok}"},
                       json={"name": "socialauto-auto"}, timeout=20).json()
    app_pw = pw.get("password", "")
    if not app_pw:
        print("app-password mint failed:", str(pw)[:120]); return 1
    import subprocess
    r = subprocess.run(
        ["python3", f"{SKILLS}/bluesky-ops/scripts/bsky_provision.py",
         "connect", "--handle", sess["handle"], "--password", app_pw],
        capture_output=True, text=True, timeout=60)
    print("provision:", "ok" if r.returncode == 0 else r.stderr[:150])
    return r.returncode


if __name__ == "__main__":
    sys.exit({"run": run, "check": check}[sys.argv[1] if len(sys.argv) > 1 else "check"]())
