#!/usr/bin/env python3
"""Dump the LinkedIn dev-portal mdp-settings (ad accounts) page DOM surface."""
import json, os, sys, time, urllib.request
import websocket

CDP = os.environ.get("CDP_URL", "http://localhost:9229")
URL = "https://www.linkedin.com/developers/apps/264925843/products/mdp-settings"

def ws_conn(url):
    return websocket.create_connection(url, timeout=30, suppress_origin=True)

def ev(ws, expr, mid=[0]):
    mid[0]+=1
    ws.send(json.dumps({"id":mid[0],"method":"Runtime.evaluate",
        "params":{"expression":expr,"returnByValue":True,"awaitPromise":True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id")==mid[0]:
            r=m.get("result",{})
            if "exceptionDetails" in r: return {"ERR": r["exceptionDetails"].get("text")}
            return r.get("result",{}).get("value")

def main():
    ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=10).read())
    b = ws_conn(ver["webSocketDebuggerUrl"])
    b.send(json.dumps({"id":1,"method":"Target.createTarget","params":{"url":URL}}))
    tid = json.loads(b.recv())["result"]["targetId"]
    time.sleep(10)
    tabs = json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())
    tab = next(t for t in tabs if t.get("id")==tid)
    ws = ws_conn(tab["webSocketDebuggerUrl"])
    out = ev(ws, """
(() => {
  const els=[...document.querySelectorAll('a,button,[role=button],input,select,textarea,label')];
  const seen=new Set(); const rows=[];
  for (const e of els){
    const t=(e.innerText||e.value||e.getAttribute('aria-label')||e.placeholder||'').trim().slice(0,100);
    const k=e.tagName+'|'+t+'|'+(e.id||'');
    if(seen.has(k)) continue; seen.add(k);
    rows.push({tag:e.tagName,txt:t,id:e.id||null,name:e.name||null,type:e.type||null,
               href:e.href||null,disabled:e.disabled??e.getAttribute('aria-disabled'),
               ph:e.placeholder||null});
  }
  return {url:location.href,title:document.title,body:(document.body.innerText||'').slice(0,1500),rows};
})()""")
    print(json.dumps(out, indent=1)[:9000])

if __name__ == "__main__":
    main()
