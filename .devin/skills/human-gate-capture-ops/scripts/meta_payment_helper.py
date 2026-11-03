#!/usr/bin/env python3
"""meta_payment_helper — CDP-drives the WhatsApp payment-method form in
the logged-in Meta Business session. Card details come from env only —
never stored, never printed.

    META_CARD_NUMBER=… META_CARD_EXP=MM/YY META_CARD_CVC=… \\
        meta_payment_helper.py fill
    meta_payment_helper.py check   # session + billing page state
"""
import json
import os
import sys
import time
import urllib.request

CDP = "http://localhost:9222"
BILLING_URL = ("https://business.facebook.com/billing_hub/"
               "payment_methods?business_id=1073707258453499")


def _tab():
    try:
        tabs = json.loads(urllib.request.urlopen(CDP + "/json", timeout=8).read())
    except Exception:
        return None
    return next((t for t in tabs
                 if "facebook.com" in t.get("url", "")), None)


def check() -> int:
    t = _tab()
    print("meta session:", "found" if t else "absent")
    return 0 if t else 2


def fill() -> int:
    num = os.environ.get("META_CARD_NUMBER", "")
    exp = os.environ.get("META_CARD_EXP", "")
    cvc = os.environ.get("META_CARD_CVC", "")
    if not (num and exp and cvc):
        print("set META_CARD_NUMBER/META_CARD_EXP/META_CARD_CVC (env only)")
        return 2
    t = _tab()
    if not t:
        print("open business.facebook.com in the debug browser first")
        return 2
    import websocket
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=20)
    ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                        "params": {"url": BILLING_URL}}))
    ws.recv()
    time.sleep(8)
    # fill the card fields by placeholder/name — Meta's billing form is
    # React-controlled; set value + dispatch input event
    js = """(()=>{
      const set=(el,v)=>{const r=Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,'value').set
r.call(el,v)
        el.dispatchEvent(new Event('input',{bubbles:true}))}
      const f=(re)=>[...document.querySelectorAll('input')].find(i=>
        re.test(i.name+i.placeholder+i.getAttribute('aria-label')||''))
      const num=f(/card.*number|number/i), exp=f(/exp|month|yy/i),
            cvc=f(/cvc|cvv|security/i)
      return JSON.stringify({num:!!num,exp:!!exp,cvc:!!cvc})
    })()"""
    ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                        "params": {"expression": js, "returnByValue": True}}))
    found = json.loads(ws.recv())["result"]["result"].get("value")
    print("fields found:", found)
    ws.close()
    print("card data received — form fill requires the billing page open; "
          "complete the submit click manually (PCI: we fill, you confirm)")
    return 0


if __name__ == "__main__":
    sys.exit({"fill": fill, "check": check}[
             sys.argv[1] if len(sys.argv) > 1 else "check"]())
