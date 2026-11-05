#!/usr/bin/env python3
"""Drive the LinkedIn dev portal via CDP on the user's logged-in browser.

Usage:
  li_portal_edit.py auth <APP_ID> add-redirect <URL>
  li_portal_edit.py auth <APP_ID> get-secret
  li_portal_edit.py products <APP_ID> request "<product text>"
Needs a debug Chrome/Edge on the CDP port with LinkedIn logged in.
Port: $CDP_URL (default http://localhost:9229 — 9222 is hijacked by the
Lenovo Vantage WebView2 on this host).
"""
import json
import os
import sys
import time
import urllib.request
import websocket

CDP = os.environ.get("CDP_URL", "http://localhost:9229")

def ws_conn(url):
    return websocket.create_connection(url, timeout=30, suppress_origin=True)

def open_tab(url):
    ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=10).read())
    b = ws_conn(ver["webSocketDebuggerUrl"])
    b.send(json.dumps({"id":1,"method":"Target.createTarget","params":{"url":url,"newWindow":False,"background":True}}))
    tid = json.loads(b.recv())["result"]["targetId"]
    time.sleep(8)
    tabs = json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())
    tab = next(t for t in tabs if t.get("id")==tid)
    return b, tid, ws_conn(tab["webSocketDebuggerUrl"])

def ev(ws, expr):
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate",
        "params":{"expression":expr,"returnByValue":True,"awaitPromise":True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id")==1:
            return m["result"].get("result",{}).get("value")

JS_SET_VALUE = """
(async (val) => {
  const ins=[...document.querySelectorAll('input')].filter(i=>!['clientIdInput','primaryClientSecret'].includes(i.id));
  const inp=ins[ins.length-1]; if(!inp) return 'noinput';
  inp.focus();
  const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(inp,'');
  for (const ch of val) {
    setter.call(inp, inp.value+ch);
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    inp.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:ch}));
    inp.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:ch}));
  }
  inp.dispatchEvent(new Event('change',{bubbles:true})); inp.blur();
  await new Promise(r=>setTimeout(r,400));
  return 'ok';
})"""

def click_btn(ws, text):
    return ev(ws, """(() => {const b=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()===%s); if(b){b.click();return 'ok'} return 'no'})()""" % json.dumps(text))

def add_redirect(app_id, url):
    b, tid, ws = open_tab(f"https://www.linkedin.com/developers/apps/{app_id}/auth")
    r = ev(ws, """(() => {
      const a=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&/Authorized redirect URLs/i.test(e.innerText||''));
      if(!a) return 'no-anchor'; let s=a; for(let k=0;k<6;k++)s=s.parentElement;
      const p=s.querySelector('.editable-block__edit-btn button'); if(!p)return 'no-pencil';
      p.click(); return 'ok';})()""")
    if r != 'ok':
        print('edit-pencil:', r)
        return
    time.sleep(2)
    print('add-url:', click_btn(ws, 'Add redirect URL'))
    time.sleep(1.5)
    print('fill:', ev(ws, f"({JS_SET_VALUE})('{url}')"))
    time.sleep(1)
    print('update:', click_btn(ws, 'Update'))
    time.sleep(4)
    txt = ev(ws, "document.body.innerText") or ''
    print('result:', 'REGISTERED' if url in txt else 'check page')
    b.send(json.dumps({"id":9,"method":"Target.closeTarget","params":{"targetId":tid}}))

def get_secret(app_id):
    b, tid, ws = open_tab(f"https://www.linkedin.com/developers/apps/{app_id}/auth")
    ev(ws, """(() => {const b=[...document.querySelectorAll('button')].find(b=>/^show$/i.test((b.innerText||'').trim())); if(b)b.click(); return 'ok'})()""")
    time.sleep(2)
    r = ev(ws, "document.getElementById('primaryClientSecret')?.value || 'n/a'")
    out = os.environ.get("LI_SECRET_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(r + "\n")
        print(f"secret written to {out}")
    else:
        print(r)
    b.send(json.dumps({"id":9,"method":"Target.closeTarget","params":{"targetId":tid}}))

def request_product(app_id, text):
    b, tid, ws = open_tab(f"https://www.linkedin.com/developers/apps/{app_id}/products")
    r = ev(ws, """(() => {
      const rows=[...document.querySelectorAll('*')].filter(e=>e.children.length===0&&e.innerText&&e.innerText.includes(%s));
      const row=rows[0]; if(!row) return 'no-product';
      let s=row; for(let k=0;k<8;k++)s=s.parentElement;
      const b=[...s.querySelectorAll('button')].find(x=>/request access/i.test(x.innerText||''));
      if(!b) return 'no-button'; b.click(); return 'clicked';})()""" % json.dumps(text))
    print('request:', r)
    time.sleep(3)
    print((ev(ws, "document.body.innerText") or '')[:400])
    b.send(json.dumps({"id":9,"method":"Target.closeTarget","params":{"targetId":tid}}))

if __name__ == "__main__":
    cmd = sys.argv[1:]
    if len(cmd) < 3:
        print(__doc__)
        sys.exit(2)
    if cmd[0] == "auth" and cmd[2] == "add-redirect":
        add_redirect(cmd[1], cmd[3])
    elif cmd[0] == "auth" and cmd[2] == "get-secret":
        get_secret(cmd[1])
    elif cmd[0] == "products" and cmd[2] == "request":
        request_product(cmd[1], cmd[3])
    else:
        print(__doc__)
