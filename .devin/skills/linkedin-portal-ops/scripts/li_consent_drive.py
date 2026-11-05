#!/usr/bin/env python3
"""Drive a staged LinkedIn consent URL through the sidecar browser session.

  li_consent_drive.py <consent-url-file>

Uses linkedin-browser-sidecar :9225 /debug/navigate + /debug/eval +
/debug/click. If the page lands on uas/login or a chrome-error redirect loop,
the session is dead — a human must log into the sidecar (noVNC) once; rerun
this script afterwards.
"""
import json
import sys
import time
import urllib.request

SC = "http://localhost:9225"

def post(path, body):
    r = urllib.request.urlopen(urllib.request.Request(
        SC + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}), timeout=30)
    return json.loads(r.read())

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    url = open(sys.argv[1]).read().strip()
    try:
        post("/debug/navigate", {"url": url})
    except Exception as e:
        print("nav error:", e)
        if "TOO_MANY_REDIRECTS" in str(e):
            print("session dead — log into the sidecar first, then rerun")
        return
    time.sleep(7)
    r = post("/debug/eval", {"script":
        "({url:location.href,btns:[...document.querySelectorAll('button')]"
        ".map(b=>(b.innerText||'').trim()).filter(Boolean).slice(0,8)})"})
    res = r.get("result", {})
    print("page:", res.get("url", "")[:160])
    if "uas/login" in (res.get("url") or ""):
        print("login wall — human login needed")
        return
    allow = post("/debug/eval", {"script":
        "(()=>{const b=[...document.querySelectorAll('button')].find("
        "b=>/allow|authorize|agree/i.test(b.innerText||''));"
        "if(b){b.click();return 'clicked'}return 'none'})()"})
    print("allow:", allow)
    time.sleep(7)
    fin = post("/debug/eval", {"script": "location.href"})
    print("final:", fin.get("result", "")[:200])
    if "code=" in str(fin.get("result") or ""):
        print("CONSENT OK — callback received code")
    elif "error=" in str(fin.get("result") or ""):
        print("scope rejected — trim with li_consent_trim.py and rerun")

if __name__ == "__main__":
    main()
