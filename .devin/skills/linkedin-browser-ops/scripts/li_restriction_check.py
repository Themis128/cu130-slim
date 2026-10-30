#!/usr/bin/env python3
"""li_restriction_check — read WHAT LinkedIn's restriction page says.

Diagnostic only: opens linkedin.com on the debug Chrome (CDP :9222) using
the user's REAL logged-in profile — no posting, no automation traffic, just
reading the banner text the account shows. Tells the user which path the
restriction needs (wait-it-out vs identity-verify vs appeal).

Usage: python3 li_restriction_check.py
"""
import json, sys, urllib.request

CDP = "http://127.0.0.1:9222"

def _tabs():
    try:
        return json.loads(urllib.request.urlopen(f"{CDP}/json", timeout=5).read())
    except Exception as e:
        sys.exit(f"CDP down — run start-chrome-debug.bat once ({e})")

def _ws_eval(ws_url, expr):
    import websocket  # websocket-client
    ws = websocket.create_connection(ws_url, timeout=15)
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
    while True:
        m=json.loads(ws.recv())
        if m.get("id")==1: ws.close(); return m.get("result",{}).get("result",{}).get("value")

def main():
    tabs=_tabs()
    page=next((t for t in tabs if t.get("type")=="page"),None) or tabs[0]
    _ws_eval(page["webSocketDebuggerUrl"],
             'location.href="https://www.linkedin.com/feed/"')
    import time; time.sleep(5)
    body=_ws_eval(page["webSocketDebuggerUrl"],
        'document.body.innerText.slice(0,1500)')
    url=_ws_eval(page["webSocketDebuggerUrl"],'location.href')
    print("URL:",url)
    print("PAGE:",(body or "")[:1000])
    if any(k in (body or "").lower() for k in ("restricted","verify","identity","appeal","temporarily")):
        print("\nDIAGNOSIS: restriction/verify prompt present — copy the banner text above")
    elif "linkedin.com/login" in (url or ""):
        print("\nDIAGNOSIS: session expired — needs a normal login (may show the restriction after)")
    else:
        print("\nDIAGNOSIS: no obvious restriction banner — account may already be clear")

if __name__=="__main__":
    main()
