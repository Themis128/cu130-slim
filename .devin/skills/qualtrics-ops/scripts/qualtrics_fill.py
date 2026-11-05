#!/usr/bin/env python3
"""Map + fill LinkedIn Qualtrics forms via CDP on the user's Edge (:9222).

  qualtrics_fill.py --map <form-url|tid>        dump question->QID map
  qualtrics_fill.py --answers a.json <url|tid>  fill {QID: "text"|[radio idx]|[checkbox idxs]}
  qualtrics_fill.py --country Greece <url|tid>  pick the custom dropdown option
  qualtrics_fill.py --submit <url|tid>          click Next/Submit

Form structure (verified on Lead Sync / Matched Audiences JFE forms):
  questions are `section.question` / `div.question` with id `question-QID<n>`;
  radios are `mc-choice-input-QID<n>-<k>`; text inputs have no id — target by
  question section. Country is a custom dropdown (click, then [role=option]).
"""
import json
import sys
import time
import urllib.request

import websocket

CDP = "http://localhost:9222"


def _ws(url):
    return websocket.create_connection(url, timeout=30, suppress_origin=True)


def open_tab(url_or_tid):
    tabs = json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())
    t = next((x for x in tabs if x.get("id") == url_or_tid), None)
    if not t:
        ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=10).read())
        b = _ws(ver["webSocketDebuggerUrl"])
        b.send(json.dumps({"id": 1, "method": "Target.createTarget",
                           "params": {"url": url_or_tid, "newWindow": False, "background": True}}))
        tid = json.loads(b.recv())["result"]["targetId"]
        time.sleep(8)
        tabs = json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())
        t = next(x for x in tabs if x.get("id") == tid)
    return _ws(t["webSocketDebuggerUrl"])


def ev(ws, expr):
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True,
                                   "awaitPromise": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            return m["result"].get("result", {}).get("value")


SECTIONS_JS = """[...document.querySelectorAll('[id^="question-"]')]
  .map(s=>({qid:s.id.replace(/^question-/,''),
            label:(s.innerText||'').trim().slice(0,140)}))"""

FILL_JS = """((qid,val)=>{
  const s=document.getElementById('question-'+qid);
  if(!s) return 'no-question';
  const txt=s.querySelector('input[type=text],input[type=email],input:not([type]),textarea');
  if(typeof val==='string'){
    if(!txt) return 'no-text-input';
    const proto=txt.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    const setter=Object.getOwnPropertyDescriptor(proto,'value').set;
    txt.focus(); setter.call(txt,val);
    txt.dispatchEvent(new Event('input',{bubbles:true}));
    txt.dispatchEvent(new Event('change',{bubbles:true})); txt.blur();
    return 'filled:'+val.slice(0,40);
  }
  const idxs=Array.isArray(val)?val:[val];
  const inputs=[...s.querySelectorAll('input[type=radio],input[type=checkbox]')];
  for(const i of idxs){
    const inp=inputs[i]||document.getElementById('mc-choice-input-'+qid+'-'+(i+1));
    if(!inp) return 'no-option-'+i;
    (inp.closest('label')||inp).click(); inp.click();
  }
  return 'clicked:'+idxs.join(',');
})"""


def cmd_map(ws):
    secs = ev(ws, SECTIONS_JS) or []
    for s in secs:
        print(f"{s['qid']:10s} {s['label']}")
    print(f"{len(secs)} questions")
    return 0


def cmd_answers(ws, answers_file):
    answers = json.load(open(answers_file, encoding="utf-8"))
    for qid, val in answers.items():
        r = ev(ws, f"({FILL_JS})({json.dumps(qid)},{json.dumps(val)})")
        print(f"{qid}: {r}")
    return 0


def cmd_country(ws, country):
    r = ev(ws, """(()=>{
      const s=[...document.querySelectorAll('[id^="question-"]')]
        .find(q=>/country/i.test(q.innerText||''));
      if(!s) return 'no-country-question';
      const c=s.querySelector('[role=combobox],select,[class*=dropdown],[tabindex]');
      if(!c) return 'no-dropdown';
      c.click(); return 'opened';})()""")
    if r != 'opened':
        print(r)
        return 1
    time.sleep(1.5)
    r = ev(ws, """((c)=>{const o=[...document.querySelectorAll('[role=option],[role=listbox] li,li')]
      .find(e=>(e.innerText||'').trim().toLowerCase()===c.toLowerCase());
      if(!o) return 'no-option'; o.click(); return 'picked';})(""" + json.dumps(country) + ")")
    print(r)
    return 0 if r == 'picked' else 1


def cmd_submit(ws):
    r = ev(ws, """(()=>{const b=[...document.querySelectorAll('button,input[type=submit],[role=button]')]
      .find(b=>/next|submit|continue/i.test(b.textContent||b.value||''));
      if(!b) return 'no-button'; b.click(); return b.textContent||b.value;})()""")
    print(r)
    return 0 if r != 'no-button' else 1


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    target = a[-1] if not a[-1].startswith("--") else None
    if not target:
        print("need <form-url|tid>")
        return 2
    ws = open_tab(target)
    try:
        if "--map" in a:
            return cmd_map(ws)
        if "--answers" in a:
            return cmd_answers(ws, a[a.index("--answers") + 1])
        if "--country" in a:
            return cmd_country(ws, a[a.index("--country") + 1])
        if "--submit" in a:
            return cmd_submit(ws)
        print(__doc__)
        return 2
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main())
