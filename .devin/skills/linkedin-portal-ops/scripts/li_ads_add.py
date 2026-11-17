#!/usr/bin/env python3
"""Click 'Add Ad Account' on mdp-settings, dump the modal/form, fill account id,
submit, and report result. Usage: li_ads_add.py [account_id]"""
import json, os, sys, time, urllib.request
import websocket

CDP = os.environ.get("CDP_URL", "http://localhost:9229")
URL = "https://www.linkedin.com/developers/apps/264925843/products/mdp-settings"
ACCT = sys.argv[1] if len(sys.argv) > 1 else "512642510"
DRY = "--execute" not in sys.argv

def ws_conn(url):
    return websocket.create_connection(url, timeout=30, suppress_origin=True)

_id = [0]
def ev(ws, expr):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id[0]:
            r = m.get("result", {})
            if "exceptionDetails" in r:
                return {"ERR": r["exceptionDetails"].get("text"), "ex": r["exceptionDetails"].get("exception", {}).get("description", "")[:300]}
            return r.get("result", {}).get("value")

def click_text(ws, text):
    return ev(ws, f"""
(() => {{
  const els=[...document.querySelectorAll('button,[role=button],a')];
  const e=els.find(x=>((x.innerText||'').trim())==={json.dumps(text)} && !x.disabled);
  if(!e) return 'NOTFOUND';
  e.scrollIntoView({{block:'center'}}); e.click();
  return 'clicked';
}})()""")

def dump_form(ws):
    return ev(ws, """
(() => {
  const els=[...document.querySelectorAll('input,button,[role=button],select,textarea,label')];
  return els.map(e=>({tag:e.tagName,txt:(e.innerText||e.value||e.getAttribute('aria-label')||e.placeholder||'').trim().slice(0,90),
    id:e.id||null,name:e.name||null,type:e.type||null,disabled:e.disabled??e.getAttribute('aria-disabled')}))
    .filter(x=>x.txt||x.id);
})()""")

def set_input(ws, selector_js, value):
    return ev(ws, f"""
(async () => {{
  const inp={selector_js};
  if(!inp) return 'noinput';
  inp.focus();
  const proto=inp.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;
  const setter=Object.getOwnPropertyDescriptor(proto,'value').set;
  setter.call(inp,'');
  for (const ch of {json.dumps(value)}) {{
    setter.call(inp, inp.value+ch);
    inp.dispatchEvent(new Event('input',{{bubbles:true}}));
    inp.dispatchEvent(new KeyboardEvent('keydown',{{bubbles:true,key:ch}}));
    inp.dispatchEvent(new KeyboardEvent('keyup',{{bubbles:true,key:ch}}));
  }}
  inp.dispatchEvent(new Event('change',{{bubbles:true}})); inp.blur();
  await new Promise(r=>setTimeout(r,400));
  return 'set:'+inp.value;
}})()""")

def main():
    ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=10).read())
    b = ws_conn(ver["webSocketDebuggerUrl"])
    b.send(json.dumps({"id":1,"method":"Target.createTarget","params":{"url":URL}}))
    tid = json.loads(b.recv())["result"]["targetId"]
    time.sleep(9)
    tabs = json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())
    tab = next(t for t in tabs if t.get("id")==tid)
    ws = ws_conn(tab["webSocketDebuggerUrl"])

    print("click Add Ad Account:", click_text(ws, "Add Ad Account"))
    time.sleep(3)
    form = dump_form(ws)
    print(json.dumps(form, indent=1)[:5000])
    if DRY:
        print("DRY RUN — not submitting")
        return

    # try to find the account-id input (heuristics: visible text input in modal)
    sel = ev(ws, """
(() => {
  const ins=[...document.querySelectorAll('input')].filter(i=>i.offsetParent!==null && !['hidden','checkbox','radio'].includes(i.type));
  if(!ins.length) return null;
  // prefer input whose nearby label/placeholder mentions account/id
  const scored=ins.map(i=>{
    const ctx=((i.closest('div,section,form')||document).innerText||'').slice(0,300).toLowerCase();
    const score=/account|sponsored|id|urn/.test(ctx)?1:0;
    return {el:i,score};
  }).sort((a,b)=>b.score-a.score);
  window.__acctInp=scored[0].el;
  return scored[0].el.id||scored[0].el.name||scored[0].el.placeholder||'idx';
})()""")
    print("input marker:", sel)
    if sel in (None, "null"):
        print("no input found"); return
    # set via stored element ref — re-evaluate using window.__acctInp
    r = ev(ws, f"""
(async () => {{
  const inp=window.__acctInp; if(!inp) return 'lost';
  inp.focus();
  const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(inp,'');
  for (const ch of {json.dumps(ACCT)}) {{
    setter.call(inp, inp.value+ch);
    inp.dispatchEvent(new Event('input',{{bubbles:true}}));
    inp.dispatchEvent(new KeyboardEvent('keydown',{{bubbles:true,key:ch}}));
    inp.dispatchEvent(new KeyboardEvent('keyup',{{bubbles:true,key:ch}}));
  }}
  inp.dispatchEvent(new Event('change',{{bubbles:true}})); inp.blur();
  await new Promise(r=>setTimeout(r,500));
  return 'set:'+inp.value;
}})()""")
    print("fill:", r)
    time.sleep(1)
    # click submit-ish button inside modal (Add/Save/Submit/Confirm)
    sub = ev(ws, """
(() => {
  const cands=[...document.querySelectorAll('button,[role=button]')].filter(b=>b.offsetParent!==null && !b.disabled);
  const labels=cands.map(b=>(b.innerText||'').trim());
  const pick=cands.find(b=>/^(add|save|submit|confirm|done|create)/i.test((b.innerText||'').trim()) && !/ad account$/.test((b.innerText||'').trim()));
  window.__submitBtn=pick||null;
  return {labels, picked: pick?(pick.innerText||'').trim():null};
})()""")
    print("submit candidates:", json.dumps(sub))
    if isinstance(sub, dict) and sub.get("picked"):
        print("submit click:", ev(ws, "(()=>{window.__submitBtn.click();return 'ok'})()"))
        time.sleep(6)
        print("post body:", ev(ws, "document.body.innerText.slice(0,1200)"))

if __name__ == "__main__":
    main()
