#!/usr/bin/env python3
"""Drive the TikTok Business Center verification resubmit via the
engagement-browser sidecar (real DOM — the app WebView refuses injected taps).
Prereq: TikTok session in the sidecar (run login-bootstrap.py first)."""
import json
import subprocess
import time

S = "http://localhost:9223"

def post(p, b):
    r = subprocess.run(["docker","exec","engagement-browser","curl","-s","-X","POST",
        S+p,"-H","Content-Type: application/json","-d",json.dumps(b)],
        capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return {}

def get(p):
    r = subprocess.run(["docker","exec","engagement-browser","curl","-s",S+p],
                       capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return {}

def js(e): return post("/session/evaluate", {"expression": e})

post("/session/navigate", {"url": "https://business.tiktok.com/"})
time.sleep(7)
print("page:", js("document.title").get("result"))

res = js("""(function(){
    var els=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='Edit');
    if(els.length){els[0].click();return 'clicked'}
    return 'edit not found — may already be logged out or no rejected card'
})()""")
print("edit:", res.get("result"))
time.sleep(5)
print("now:", js("location.href").get("result"))
