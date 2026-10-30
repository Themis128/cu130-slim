#!/usr/bin/env python3
"""qualtrics_fill.py — drive a Qualtrics access-request form on the
dedicated adhoc browser (:9232) from a selector→value map.

Same session-slot as bridge_approve — no shared-session contention.
Fills text/textarea fields by position or selector, ticks radios, submits.

    qualtrics_fill.py <url> '<json field-map>'
    # field-map: {"0": "Cloudless.gr", "1": "tbaltzakis@cloudless.gr", ...}
    #            (index = nth fillable field on the page)
    qualtrics_fill.py <url> --probe   # dump the field list only
"""
import json
import sys
import time
import urllib.request

BRIDGE = "http://localhost:9232"


def _call(path: str, body: dict | None = None, t: float = 25.0) -> dict:
    r = urllib.request.Request(f"{BRIDGE}{path}",
                               data=json.dumps(body or {}).encode(),
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f:
            return json.loads(f.read() or b"{}")
    except Exception as e:
        return {"error": f"{type(e).__name__}"}


def _ev(js: str) -> str:
    r = _call("/session/evaluate", {"expression": js})
    return str(r.get("result") or r.get("error") or "?")[:300]


def probe() -> str:
    return _ev(
        "[...document.querySelectorAll('input[type=text],input[type=email],"
        "textarea')].map((e,i)=>i+':'+((e.getAttribute('aria-label')||e.name||"
        "e.placeholder||'').slice(0,45))).join(' ;; ')")


def fill(fmap: dict) -> str:
    out = []
    for k, v in fmap.items():
        js = (
            "(()=>{var f=[...document.querySelectorAll('input[type=text],"
            "input[type=email],textarea')];var e=f[" + str(int(k)) + "];"
            "if(!e)return 'no-field-" + str(k) + "';"
            "e.focus();e.value=" + json.dumps(v) + ";"
            "e.dispatchEvent(new Event('input',{bubbles:true}));"
            "e.dispatchEvent(new Event('change',{bubbles:true}));"
            "return 'set'})()")
        out.append(f"{k}:{_ev(js)[:40]}")
        time.sleep(0.4)
    return ",".join(out)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: qualtrics_fill.py <url> [field-map|json|--probe]")
        return 2
    url = sys.argv[1]
    _call("/session/start", {"platform": "twitter"})
    time.sleep(2)
    _ev(f"location.href={json.dumps(url)}")
    time.sleep(8)
    if "--probe" in sys.argv:
        print(probe())
        return 0
    fmap = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print("fill:", fill(fmap))
    # next/submit — Qualtrics uses a #NextButton
    print("next:", _ev(
        "(()=>{var b=document.getElementById('NextButton')||"
        "[...document.querySelectorAll('button,input[type=button]')]"
        ".find(e=>/next|submit|finish/i.test(e.innerText||e.value||''));"
        "b&&b.click();return b?'clicked':'no-next'})()"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
