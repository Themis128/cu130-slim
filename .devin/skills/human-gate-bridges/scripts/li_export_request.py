#!/usr/bin/env python3
"""li_export_request.py — click LinkedIn's 'Request data export' via the
shared browser bridge (same /session/evaluate pattern as skool-ops).
Requires an active LinkedIn session on the bridge."""
import json
import sys
import urllib.request

BRIDGE = "http://browser-novnc:9223"



def pin() -> None:
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                "http://stack-ops:8787/keepawake/browser-novnc?ttl=600",
                method="POST", data=b""), timeout=8)
    except Exception:
        pass

def ev(expr: str, tries: int = 8) -> dict:
    # 409 = another platform session holds the shared bridge — back off.
    last = None
    for i in range(tries):
        r = urllib.request.Request(
            BRIDGE + "/session/evaluate",
            data=json.dumps({"expression": expr}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as f:
                return json.loads(f.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 409:
                time.sleep(30)  # noqa: F821
                continue
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}
    return {"error": f"bridge busy after {tries} tries: {last}"}

def main() -> int:
    pin()
    # navigate to the data-privacy export page
    ev("location.href='https://www.linkedin.com/mypreferences/d/categories/data-privacy'")
    import time
    time.sleep(5)
    # click the 'Get a copy of your data' → request export flow
    out = ev("""
    (()=>{
      const click=(txt)=>{const el=[...document.querySelectorAll('button,li,a,span,div[role=button]')]
        .find(e=>e.innerText&&e.innerText.trim().toLowerCase().includes(txt))
if(el){el.click()
return true}return false}
      click('get a copy of your data')||click('data export')||click('export')
      return document.title
    })()
    """)
    print("page:", out.get("result") or out.get("url") or out)
    time.sleep(4)
    # confirm the request (the second-step 'Request archive'/'Done' button)
    out2 = ev("""
    (()=>{
      const b=[...document.querySelectorAll('button')]
        .find(e=>/request|done|export|submit/i.test(e.innerText||''))
      if(b){b.click();return 'clicked:'+b.innerText.slice(0,40)}
      return 'no confirm button found — session may need login or UI changed'
    })()
    """)
    res = out2.get("result") or out2.get("url") or out2
    print("result:", res)
    return 0 if "clicked" in str(res) else 1

if __name__ == "__main__":
    sys.exit(main())
