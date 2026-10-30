#!/usr/bin/env python3
"""qualtrics_fill.py — label-driven Qualtrics form filler on the browser bridge.

    qualtrics_fill.py --url <form> --answers answers.json [--bridge URL]

answers.json: {"<label substring>": "<value>"} for text fields,
              {"<label substring>": "<choice index 1..n>"} for radios.

Qualtrics fields are anonymous (no ids) — matching is by label text, the
only stable anchor. Radios use mc-choice-input-QIDx-y (click yth choice).
Navigates pages via the Next button until Submit (or stops with --dry).
"""
import json
import sys
import time
import urllib.request
import urllib.error

BRIDGE = "http://browser-novnc-adhoc:9224"

def call(path, data, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(f"{BRIDGE}{path}",
                data=json.dumps(data).encode(),
                headers={"Content-Type":"application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=45).read())
        except urllib.error.HTTPError as e:
            if e.code in (409,) or (e.code == 500 and b"context was destroyed" in e.read()):
                time.sleep(15); continue
            raise SystemExit(f"bridge {e.code}: {e.read()[:120]}")
    raise SystemExit(f"bridge {path}: all {tries} tries failed (409/context-destroyed)")

def ev(expr):
    return call("/session/evaluate", {"expression": expr}).get("result")

FILL_JS = """
(()=>{const label=%s, value=%s;
const el=[...document.querySelectorAll('.question-display,.QuestionText,label,legend,div')]
 .find(e=>e.children.length<6&&e.textContent.trim().toLowerCase().includes(label.toLowerCase()));
if(!el) return 'NO_FIELD:'+label;
let q=el;
while(q&&!q.querySelector('input,textarea')&&q.parentElement)q=q.parentElement;
if(!q||!q.querySelector('input,textarea'))return 'NO_INPUT:'+label;
const txt=q.querySelector('input[type=text],textarea');
if(txt){txt.focus();txt.value=value;['input','keyup','change'].forEach(t=>txt.dispatchEvent(new Event(t,{bubbles:true})));return 'filled:'+label;}
const radios=[...q.querySelectorAll('input[type=radio]')];
const i=parseInt(value)-1;
if(radios[i]){const lbl=radios[i].closest('label')||radios[i].parentElement;lbl.click();radios[i].click();return 'radio:'+label+'->'+(i+1);}
const checks=[...q.querySelectorAll('input[type=checkbox]')];
if(checks.length&&i>=0&&checks[i]){checks[i].click();return 'check:'+label;}
return 'NO_INPUT:'+label;})()"""

NEXT_JS = """(()=>{const b=[...document.querySelectorAll('button,input[type=submit],[role=button]')]
 .find(b=>/next|submit|continue/i.test(b.textContent||b.value||''));
 if(b){b.click();return b.textContent||b.value;} return 'NO_NEXT'})()"""

def main():
    global BRIDGE
    url = answers_file = None
    dry = "--dry" in sys.argv
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--url": url = a[i+1]
        if x == "--answers": answers_file = a[i+1]
        if x == "--bridge": BRIDGE = a[i+1]
    if not url or not answers_file:
        raise SystemExit("need --url + --answers")
    with open(answers_file) as _f:
        answers = json.load(_f)
    try: call("/session/close", {})
    except SystemExit: pass
    call("/session/start", {"platform":"reddit","interactive":False,"force":True})
    time.sleep(4)
    call("/session/navigate", {"url": url})
    time.sleep(12)
    for page in range(8):
        for label, val in answers.items():
            r = ev(FILL_JS % (json.dumps(label), json.dumps(str(val))))
            if r and r.startswith(("NO_FIELD","NO_INPUT")):
                pass  # field on a later page — retried next page
        if dry:
            print("dry — stopping before Next"); return 0
        r = ev(NEXT_JS)
        print(f"page {page+1}: next={r}")
        if not r or r == "NO_NEXT" or "submit" in r.lower():
            break
        time.sleep(6)
    print("done — review then click Submit manually")
    return 0

if __name__ == "__main__":
    sys.exit(main())
