#!/usr/bin/env python3
# ruff: noqa: BLE001 UP031 -- broad catch retries SPA context destruction; %-format injects JSON into JS without fighting f-string braces.
"""Drive LinkedIn Campaign Manager through the LinkedIn browser sidecar (port 9225).

Usage:
  linkedin_cm.py nav '<url>'            Navigate (waits for SPA settle)
  linkedin_cm.py text                   Dump rendered page text
  linkedin_cm.py eval '<js>'            Run JS in the page, print result
  linkedin_cm.py click 'Visible text'   Click button/link/menuitem by text
  linkedin_cm.py fill '<label>' 'value' React-safe fill of input by label/placeholder
  linkedin_cm.py upload <file>          Inject file into input[type=file] via DataTransfer
  linkedin_cm.py shot <path>            Save screenshot
  linkedin_cm.py retry '<js>' [n]       Eval with retries (SPA context-destruction)

Env: LINKEDIN_SIDECAR (default http://localhost:9225)
"""
import base64
import json
import os
import sys
import time
import urllib.request

SIDECAR = os.environ.get("LINKEDIN_SIDECAR", "http://localhost:9225").rstrip("/")


def post(path, payload, timeout=60):
    req = urllib.request.Request(
        SIDECAR + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path, timeout=60):
    with urllib.request.urlopen(SIDECAR + path, timeout=timeout) as r:
        body = r.read()
        ctype = r.headers.get("Content-Type", "")
        return body if "image" in ctype else json.loads(body)


def evaljs(script):
    r = post("/debug/eval", {"script": script})
    if r.get("status") != "ok":
        raise RuntimeError(f"eval failed: {r}")
    return r.get("result")


def eval_retry(script, tries=4, wait=2.5):
    last = None
    for _ in range(tries):
        try:
            return evaljs(script)
        except Exception as e:  # SPA re-navigation destroys the context
            last = e
            time.sleep(wait)
    raise last if last is not None else RuntimeError("eval_retry: no attempts ran")


def cmd_nav(url):
    print(json.dumps(post("/debug/navigate", {"url": url}, timeout=90)))
    time.sleep(8)


def cmd_text():
    r = get("/debug/page-text")
    print(r.get("text", r) if isinstance(r, dict) else r)


def cmd_click(text):
    return evaljs(
        """(()=>{const q=%s;
const els=[...document.querySelectorAll('button,a,[role=button],[role=menuitem],[role=option],li')];
const el=els.find(e=>e.innerText&&e.innerText.trim().toLowerCase().includes(q.toLowerCase()));
if(!el)return 'NOT_FOUND';
el.scrollIntoView({block:'center'});el.click();return 'clicked: '+el.innerText.trim().slice(0,80)})()"""
        % json.dumps(text)
    )


def cmd_fill(label, value):
    return evaljs(
        """(()=>{const q=%s,v=%s;
const ins=[...document.querySelectorAll('input[type=text],input:not([type]),textarea,[contenteditable=true]')];
const el=ins.find(i=>{const lbl=(i.getAttribute('aria-label')||i.placeholder||i.name||'').toLowerCase();
const wrap=i.closest('div,fieldset,section');const t=wrap?wrap.innerText.slice(0,200).toLowerCase():'';
return lbl.includes(q)||t.includes(q)});
if(!el)return 'NOT_FOUND';
const s=Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value');
if(el.isContentEditable){el.focus();el.innerText=v;el.dispatchEvent(new Event('input',{bubbles:true}));return 'filled(contenteditable)';}
s.set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
return 'filled: '+(el.getAttribute('aria-label')||el.placeholder||el.name)})()"""
        % (json.dumps(label.lower()), json.dumps(value))
    )


def cmd_upload(path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    name = os.path.basename(path)
    mime = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else (
        "image/png" if name.lower().endswith(".png") else "application/pdf"
    )
    # base64 keeps the JSON body small enough for urllib (shell arg limit bypassed)
    script = """(async()=>{const inp=document.querySelector('input[type=file]');
if(!inp)return 'NO_FILE_INPUT';
const b64=%s;const bin=atob(b64);const arr=new Uint8Array(bin.length);
for(let i=0;i<bin.length;i++)arr[i]=bin.charCodeAt(i);
const f=new File([arr],%s,{type:%s});
const dt=new DataTransfer();dt.items.add(f);inp.files=dt.files;
inp.dispatchEvent(new Event('change',{bubbles:true}));
return 'uploaded '+f.name+' ('+f.size+' bytes)'})()""" % (
        json.dumps(b64), json.dumps(name), json.dumps(mime))
    return evaljs(script)


def cmd_shot(path):
    body = get("/debug/screenshot")
    with open(path, "wb") as f:
        f.write(body if isinstance(body, bytes) else base64.b64decode(body.get("png", "")))
    return path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd, args = sys.argv[1], sys.argv[2:]
    out = {
        "nav": lambda: cmd_nav(args[0]),
        "text": cmd_text,
        "eval": lambda: print(evaljs(args[0])),
        "retry": lambda: print(eval_retry(args[0], int(args[1]) if len(args) > 1 else 4)),
        "click": lambda: print(cmd_click(args[0])),
        "fill": lambda: print(cmd_fill(args[0], args[1])),
        "upload": lambda: print(cmd_upload(args[0])),
        "shot": lambda: print(cmd_shot(args[0])),
    }.get(cmd)
    if not out:
        print(__doc__)
        sys.exit(1)
    out()


if __name__ == "__main__":
    main()
