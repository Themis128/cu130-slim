#!/usr/bin/env python3
"""x_oauth_approve.py — drive the X OAuth consent page via the browser
bridge (X session must be logged in on the bridge). Run after wire.py
prints the authorization URL.

    x_oauth_approve.py <authorization_url>
"""
import json, sys, time, urllib.request

BRIDGE = "http://browser-novnc:9223"

def ev(expr: str) -> dict:
    r = urllib.request.Request(
        BRIDGE + "/session/evaluate",
        data=json.dumps({"expression": expr}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=20) as f:
            return json.loads(f.read())
    except Exception as e:
        return {"error": str(e)}

def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 1
    url = sys.argv[1]
    ev(f"location.href={json.dumps(url)}")
    time.sleep(6)
    out = ev("""
    (()=>{
      const b=[...document.querySelectorAll('button,input[type=submit],a[role=button]')]
        .find(e=>/authorize|allow|approve|continue/i.test(e.innerText||e.value||''));
      if(b){b.click();return 'approved:'+(b.innerText||b.value).slice(0,40)}
      return 'no authorize button — login wall or UI changed';
    })()
    """)
    res = out.get("result") or out.get("url") or out
    print(res)
    # after approve, X redirects to the callback with ?code= — grab it
    time.sleep(4)
    loc = ev("location.href")
    cur = loc.get("result") or loc.get("url") or ""
    if "code=" in cur:
        print("code captured — paste back into wire.py:", cur)
    return 0 if "approved" in str(res) else 1

if __name__ == "__main__":
    sys.exit(main())
