#!/usr/bin/env python3
"""Batch-fill + verify LinkedIn Qualtrics access-request forms over CDP :9229.

Multi-round: fill flagged required questions -> Next -> rescan until "Thank you"
(max 4 rounds). Single-pass fills always leave required fields because
conditional questions only flag errors after a failed submit.

Error selector: .ValidationError,[class*=error-message],.QuestionError
(plain .ValidationError alone matches NOTHING on this Qualtrics theme).
Radio semantics on this theme: Yes=[0], No=[1], "75%+" = last %B2B option.
Skips display-wrapper-/display- prefixed duplicate nodes (responsive markup).

CRITICAL: open each form via the app's OWN products-page link — the Q_DL
query param binds the submission to that developer app; a bare
SV_xxx?Q_CHL=gl link submits an orphan response LinkedIn can't match.

Usage:
  python li_batch_fill.py <qualtrics_url> [<url> ...]
  python li_batch_fill.py            # audit+fix every open qualtrics tab
"""
import json, sys, time, urllib.request, websocket

CDP = "http://127.0.0.1:9229"

FILL = """((qid,val)=>{const s=document.getElementById('question-'+qid);if(!s)return 0;
 if(typeof val==='string'){const t=s.querySelector('input[type=text],input[type=email],input:not([type]),textarea');
  if(!t)return 0;const p=t.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  const set=Object.getOwnPropertyDescriptor(p,'value').set;t.focus();set.call(t,val);
  t.dispatchEvent(new Event('input',{bubbles:true}));t.dispatchEvent(new Event('change',{bubbles:true}));t.blur();return 1;}
 const ins=[...s.querySelectorAll('input[type=radio],input[type=checkbox]')];let c=0;
 for(const i of val){const x=ins[i];if(x){(x.closest('label')||x).click();c++;}}return c;})"""

ERR_QS = """JSON.stringify([...document.querySelectorAll('[id^="question-"]')]
 .filter(q=>!(q.id||'').includes('display'))
 .filter(q=>{const e=q.querySelector('.ValidationError,[class*=error-message],.QuestionError');
   return e&&(e.innerText||'').trim()})
 .map(q=>q.id.replace('question-','')))"""

ALL_QS = """JSON.stringify([...document.querySelectorAll('[id^="question-"]')]
 .filter(q=>!(q.id||'').includes('display'))
 .map(q=>({q:q.id.replace('question-',''),
   l:(q.innerText||'').replace(/\\s+/g,' ').trim().slice(0,110)})))"""

BIZ = ("Cloudless provides managed cloud and marketing automation for SMBs. "
       "SocialAuto is our internal product automating our LinkedIn presence: "
       "scheduling, analytics, lead capture and engagement routed into our audit funnel.")
USECASE = ("Member-facing integration: consenting users connect their LinkedIn account; "
           "SocialAuto automates publishing, analytics and lead capture on their behalf "
           "under GDPR-compliant processing.")
CLIENT_ID = "77j8kzfi1a6jn8"  # update per app if the form asks verbatim

def guess(label):
    l = label.lower()
    for k, v in [
        ('client id', CLIENT_ID),
        ('alternate legal name', 'N/A'),
        ('legal name', 'Themis Baltzakis'), ('company name', 'Themis Baltzakis'),
        ('organization address', 'Koropi, Attica 19400, Greece'),
        ('company address', 'Koropi, Attica 19400, Greece'),
        ('street', 'Koropi'), ('city', 'Koropi'),
        ('state', 'Attica'), ('province', 'Attica'),
        ('zip', '19400'), ('postal', '19400'),
        ('screen recording', 'https://social.cloudless.gr (live product; read-only demo)'),
        ('test login', 'Read-only demo: https://social.cloudless.gr'),
        ('website', 'https://cloudless.gr'), ('privacy', 'https://cloudless.gr/privacy'),
        ('tell us about your business', BIZ), ('plan to build', BIZ),
        ('intended use', USECASE), ('use case does your organization', USECASE),
        ('describe', BIZ), ('name', 'Themistoklis Baltzakis'), ('address', 'Koropi'),
    ]:
        if k in l: return v
    for k, v in [
        ('b2b', [3]), ('primary use', [0]),
        ('only your company', [1]), ('own marketing', [1]),
        ('personal data', [0]), ('other api', [0]),
        ('lead sync service', [0]), ('category best', [2]),
        ('partnership', [0]), ('why are you requesting', [0]), ('select all', [0]),
    ]:
        if k in l: return v
    return None

def ev(ws, e):
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": e, "returnByValue": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            return m.get("result", {}).get("result", {}).get("value")

def next_click(ws):
    return ev(ws, """(()=>{const b=[...document.querySelectorAll('button,input[type=submit],[role=button]')]
      .find(b=>/next|submit|continue/i.test(b.textContent||b.value||''));
      if(!b)return 'no-btn';b.click();return 'NEXT';})()""")

def submitted(ws):
    return bool(ev(ws, "/thank you/i.test(document.body.innerText)"))

def fill_form(ws, label=""):
    for rnd in range(4):
        if submitted(ws):
            print(f"  {label} SUBMITTED"); return True
        errs = json.loads(ev(ws, ERR_QS) or "[]")
        targets = errs
        if not targets:
            qs = json.loads(ev(ws, ALL_QS) or "[]")
            for q in qs:
                a = guess(q["l"])
                if a is not None:
                    ev(ws, f"({FILL})({json.dumps(q['q'])},{json.dumps(a)})")
            targets = [q["q"] for q in qs]
        else:
            for q in errs:
                lbl = ev(ws, f"(document.getElementById('question-{q}')||{{innerText:''}}).innerText.slice(0,140)") or ""
                a = guess(lbl)
                if a is None:
                    ins = ev(ws, f"document.querySelectorAll('#question-{q} input[type=radio],#question-{q} input[type=checkbox]').length") or 0
                    a = [0] if ins else BIZ
                ev(ws, f"({FILL})({json.dumps(q)},{json.dumps(a)})")
        print(f"  {label} rnd{rnd} targets:{len(targets)}")
        if next_click(ws) == "no-btn":
            print(f"  {label} NO-NEXT-BTN"); return False
        time.sleep(5)
    ok = submitted(ws)
    print(f"  {label} {'SUBMITTED' if ok else 'STILL-OPEN'}")
    return ok

def open_tab(url):
    ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=10).read())
    b = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
    b.send(json.dumps({"id": 1, "method": "Target.createTarget",
                       "params": {"url": url, "background": True}}))
    tid = json.loads(b.recv())["result"]["targetId"]
    time.sleep(10)
    tab = next(t for t in json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())
               if t.get("id") == tid)
    return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)

if __name__ == "__main__":
    urls = sys.argv[1:]
    if urls:
        for u in urls:
            sv = u.split("SV_")[1][:15] if "SV_" in u else "?"
            print(f"=== {sv}")
            fill_form(open_tab(u), sv)
    else:
        tabs = json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())
        seen = set()
        for t in tabs:
            if t.get("type") != "page" or "qualtrics.com" not in t.get("url", "") or "/jfe/form/" not in t.get("url", ""):
                continue
            sv = t["url"].split("SV_")[1][:15]
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            print(f"=== {sv}")
            fill_form(websocket.create_connection(t["webSocketDebuggerUrl"], timeout=20, suppress_origin=True), sv)
