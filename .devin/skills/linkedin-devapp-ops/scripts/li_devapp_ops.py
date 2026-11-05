"""LinkedIn dev-app lifecycle driver — CDP :9229.

  python li_devapp_ops.py create <name>
  python li_devapp_ops.py verify-company <app_id>
  python li_devapp_ops.py request <app_id> <product-heading>
  python li_devapp_ops.py tooltip <app_id> <product-heading>
  python li_devapp_ops.py fill-form <qdl_url>

Constants below are cloudless.gr — edit for another org.
"""

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

import websocket

CDP = os.environ.get("CDP_URL", "http://127.0.0.1:9229")
COMPANY = "cloudless.gr"
PRIVACY = "https://cloudless.gr/privacy"
EMAIL = "tbaltzakis@cloudless.gr"
FETCH_CODE = os.path.join(os.path.dirname(__file__), "..", "..", "mail-code-fetch", "scripts", "fetch-code.py")
BIZ = ("Cloudless provides managed cloud and marketing automation for SMBs. "
       "SocialAuto automates our LinkedIn presence: scheduling, analytics, "
       "lead capture, engagement routed into our audit funnel.")


def send(ws, m):
    ws.send(json.dumps(m))
    return json.loads(ws.recv())


def ev(ws, expr, t=20):
    r = send(ws, {"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}})
    return r.get("result", {}).get("result", {}).get("value")


def mclick(ws, x, y):
    for i, ph in enumerate(["mousePressed", "mouseReleased"]):
        send(ws, {"id": 10 + i, "method": "Input.dispatchMouseEvent",
                  "params": {"type": ph, "x": x, "y": y, "button": "left", "clickCount": 1}})


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())


def open_tab(url, bg=False):
    ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=10).read())
    bws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    tid = send(bws, {"id": 1, "method": "Target.createTarget", "params": {"url": url, "background": bg}})["result"]["targetId"]
    time.sleep(14)
    tab = next(x for x in tabs() if x.get("id") == tid)
    return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20, suppress_origin=True), tid


def settext(ws, sel, val):
    return ev(ws, f"""(()=>{{const t=document.querySelector('{sel}');if(!t)return 0;
      const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      t.focus();s.call(t,{json.dumps(val)});
      t.dispatchEvent(new Event('input',{{bubbles:true}}));t.dispatchEvent(new Event('change',{{bubbles:true}}));return 1;}})()""")


def png(n=256):
    def chunk(ty, d):
        c = ty + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    p = os.path.join(tempfile.gettempdir(), f"li_logo{n}.png")
    open(p, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x10\x3f\x6f" * n) * n)) + chunk(b"IEND", b""))
    return p


def modal(ws):
    return ev(ws, """[...document.querySelectorAll('.artdeco-modal,[role=dialog]')].find(m=>m.offsetParent)||null""") is not None


def mbtn_rect(ws, regex):
    return ev(ws, f"""(()=>{{const m=[...document.querySelectorAll('.artdeco-modal,[role=dialog]')].find(m=>m.offsetParent);
      if(!m)return 'closed';const b=[...m.querySelectorAll('button')].find(x=>/{regex}/i.test(x.innerText||''));
      if(!b)return 'no-btn';if(b.className.includes('disabled'))return 'disabled';
      const r=b.getBoundingClientRect();return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);}})()""")


def fetch_code():
    out = subprocess.run(["python", FETCH_CODE, "linkedin", "", "5m"], capture_output=True, text=True, timeout=90).stdout
    m = re.search(r"\d{6}", out)
    return m.group(0) if m else None


# ── commands ──────────────────────────────────────────────────────────


def cmd_create(name):
    ws, _ = open_tab("https://www.linkedin.com/developers/apps/new")
    time.sleep(3)
    assert ev(ws, "!!document.getElementById('createAppNameInput')"), "form not loaded"
    settext(ws, "#createAppNameInput", name)
    settext(ws, "#createAppPrivacyUrlInput", PRIVACY)
    settext(ws, "#associateCompanyTypeaheadInput", COMPANY)
    time.sleep(3)
    opts = json.loads(ev(ws, """JSON.stringify([...document.querySelectorAll('[role=option],[class*=typeahead] li,[class*=result] *')]
      .filter(o=>/""" + COMPANY.split(".")[0] + """/i.test(o.innerText||'')&&(o.innerText||'').length<80)
      .map(o=>{const r=o.getBoundingClientRect();return [Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];}))""") or "[]")
    if opts:
        mclick(ws, *opts[0])
        time.sleep(2)
    send(ws, {"id": 2, "method": "DOM.enable"})
    doc = send(ws, {"id": 3, "method": "DOM.getDocument", "params": {"depth": -1}})
    q = send(ws, {"id": 4, "method": "DOM.querySelectorAll",
                  "params": {"nodeId": doc["result"]["root"]["nodeId"], "selector": "input[type=file]"}})
    if q["result"]["nodeIds"]:
        send(ws, {"id": 5, "method": "DOM.setFileInputFiles", "params": {"files": [png()], "nodeId": q["result"]["nodeIds"][0]}})
    ev(ws, "(()=>{const c=document.querySelector('input[type=checkbox]');if(c&&!c.checked)c.click();return 1;})()")
    time.sleep(1)
    rc = ev(ws, """(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='Create app');
      b.scrollIntoView();const r=b.getBoundingClientRect();
      return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);})()""")
    x, y = json.loads(rc)
    mclick(ws, x, y)
    time.sleep(10)
    print("url:", ev(ws, "location.href||'?'"))


def cmd_verify_company(app_id):
    ws, _ = open_tab(f"https://www.linkedin.com/developers/apps/{app_id}/settings")
    rc = ev(ws, """(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='Verify');
      if(!b)return null;b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();
      return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);})()""")
    if not rc:
        return print("no Verify button — already verified?")
    x, y = json.loads(rc)
    mclick(ws, x, y)
    time.sleep(5)
    g = ev(ws, """(()=>{const m=[...document.querySelectorAll('.artdeco-modal,[role=dialog]')].find(m=>m.offsetParent);
      const b=[...m.querySelectorAll('button')].find(x=>/generate url/i.test(x.innerText||''));
      if(!b)return 'no-gen';const r=b.getBoundingClientRect();
      return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);})()""")
    if g.startswith("["):
        x, y = json.loads(g)
        mclick(ws, x, y)
        time.sleep(4)
    url = ev(ws, """(document.querySelector('.artdeco-modal')?.innerText||'').match(/https?:\\/\\/[^\\s"']+verification[^\\s"']+/)?.[0]""")
    print("verify URL:", url)
    if url:
        vws, _ = open_tab(url)
        time.sleep(3)
        rc = ev(vws, """(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='Verify');
          if(!b)return null;const r=b.getBoundingClientRect();
          return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);})()""")
        if rc:
            x, y = json.loads(rc)
            mclick(vws, x, y)
            time.sleep(6)
            print(ev(vws, "document.body.innerText.slice(0,150)"))


def cmd_request(app_id, heading):
    ws, _ = open_tab(f"https://www.linkedin.com/developers/apps/{app_id}/products")
    rc = ev(ws, f"""(()=>{{const hs=[...document.querySelectorAll('h1,h2,h3,h4,h5')].find(x=>(x.innerText||'').trim()==='{heading}');
      if(!hs)return 'no-heading';let card=hs;for(let i=0;i<10&&card;i++){{card=card.parentElement;
        if(card&&card.querySelector('button'))break;}}
      const b=card&&[...card.querySelectorAll('button')].find(x=>/request access/i.test(x.innerText||''));
      if(!b)return 'no-btn';if(b.className.includes('disabled'))return 'disabled';
      b.scrollIntoView({{block:'center'}});const r=b.getBoundingClientRect();
      return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);}})()""")
    print("card btn:", rc)
    if not rc.startswith("["):
        return
    x, y = json.loads(rc)
    mclick(ws, x, y)
    time.sleep(4)
    # email verify → code → confirm → ToS → Request — ONE modal session
    if "no-input" != ev(ws, """(()=>{const m=[...document.querySelectorAll('.artdeco-modal,[role=dialog]')].find(m=>m.offsetParent);
      return m&&m.querySelector('input[type=email],input[type=text]')?'yes':'no-input';})()"""):
        r = ev(ws, """(()=>{const m=[...document.querySelectorAll('.artdeco-modal,[role=dialog]')].find(m=>m.offsetParent);
          const inp=m.querySelector('input');
          const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
          inp.focus();s.call(inp,'""" + EMAIL + """');
          inp.dispatchEvent(new Event('input',{bubbles:true}));inp.dispatchEvent(new Event('change',{bubbles:true}));
          const b=[...m.querySelectorAll('button')].find(x=>/send verification/i.test(x.innerText||''));
          if(!b)return 'no-btn';const r=b.getBoundingClientRect();
          return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);})()""")
        if r.startswith("["):
            x, y = json.loads(r)
            mclick(ws, x, y)
            time.sleep(10)
            code = fetch_code()
            print("code:", code)
            if code:
                r = ev(ws, f"""(()=>{{const m=[...document.querySelectorAll('.artdeco-modal,[role=dialog]')].find(m=>m.offsetParent);
                  const inp=m.querySelector('input');
                  const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                  inp.focus();s.call(inp,'{code}');
                  inp.dispatchEvent(new Event('input',{{bubbles:true}}));inp.dispatchEvent(new Event('change',{{bubbles:true}}));
                  const b=[...m.querySelectorAll('button')].find(x=>/confirm/i.test(x.innerText||''));
                  const r=b.getBoundingClientRect();
                  return JSON.stringify([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);}})()""")
                if r.startswith("["):
                    x, y = json.loads(r)
                    mclick(ws, x, y)
                    time.sleep(6)
    # ToS checkbox → Request access
    ev(ws, """(()=>{const m=[...document.querySelectorAll('.artdeco-modal,[role=dialog]')].find(m=>m.offsetParent);
      const cb=m&&m.querySelector('input[type=checkbox]');if(cb&&!cb.checked)cb.click();return 1;})()""")
    time.sleep(1)
    ra = mbtn_rect(ws, "request access")
    print("req btn:", ra)
    if ra.startswith("["):
        x, y = json.loads(ra)
        mclick(ws, x, y)
        time.sleep(8)
    print("after:", ev(ws, "document.querySelector('.artdeco-modal')?.innerText.slice(0,200)||'closed'"))


def cmd_tooltip(app_id, heading):
    ws, _ = open_tab(f"https://www.linkedin.com/developers/apps/{app_id}/products")
    print(ev(ws, f"""(()=>{{const hs=[...document.querySelectorAll('h1,h2,h3,h4,h5')].find(x=>(x.innerText||'').trim()==='{heading}');
      let card=hs;for(let i=0;i<10&&card;i++){{card=card.parentElement;if(card&&card.querySelector('button'))break;}}
      const b=card&&[...card.querySelectorAll('button')].find(x=>/request access/i.test(x.innerText||''));
      if(!b)return 'no-btn';
      const tt=b.getAttribute('aria-describedby');const el=tt?document.getElementById(tt):null;
      return (el?el.innerText:'no tooltip').replace(/\\s+/g,' ').trim().slice(0,300);}})()"""))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "create":
        cmd_create(sys.argv[2])
    elif cmd == "verify-company":
        cmd_verify_company(sys.argv[2])
    elif cmd == "request":
        cmd_request(sys.argv[2], sys.argv[3])
    elif cmd == "tooltip":
        cmd_tooltip(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
