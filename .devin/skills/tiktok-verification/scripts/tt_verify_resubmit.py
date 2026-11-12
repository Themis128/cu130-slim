#!/usr/bin/env python3
"""TikTok business-verification resubmit via tiktok-browser-sidecar.

Drives the web Business Suite verification flow (no CAPTCHA — consumer
session cookies carry it). Uploads the cert via DataTransfer injection:
JS can't pick files, but Chromium lets you assign input.files from a
DataTransfer built out of a fetched/embedded File.

Usage: tt_verify_resubmit.py [cert.png]
"""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

SIDECAR = "tiktok-browser-sidecar"
API = "http://localhost:9224"
VERIFY_URL = ("https://www.tiktok.com/business-suite/business-registration/"
              "verify?source=onboarding")
CERT = Path(sys.argv[1] if len(sys.argv) > 1 else
            "/mnt/c/Users/tbaltzakis/Downloads/cert.png")
# Legal name EXACTLY as printed on the AADE certificate (Greek script).
# TikTok rejected "Baltzakis Themistoklis" (Latin) as a company-name
# inconsistency — the doc shows ΜΠΑΛΤΖΑΚΗΣ ΘΕΜΙΣΤΟΚΛΗΣ.
COMPANY_NAME = "Μπαλτζάκης Θεμιστοκλής"
LICENSE_NUMBER = "115307716"


def call(method, path, body=None):
    # Body goes over stdin — large expressions (e.g. base64 cert payloads)
    # exceed the per-argument exec limit if inlined into node -e argv.
    node_prog = f"""
let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{{
 (async()=>{{
  const body=d?JSON.parse(d):undefined;
  const r=await fetch("{API}{path}",{{method:"{method}",
   headers:{{"Content-Type":"application/json"}},body:body?JSON.stringify(body):undefined}});
  const j=await r.json();console.log(JSON.stringify(j));
 }})().catch(e=>console.log(JSON.stringify({{error:String(e)}})));
}})"""
    payload = json.dumps(body) if body is not None else ""
    out = subprocess.run(
        ["docker", "exec", "-i", SIDECAR, "node", "-e", node_prog],
        input=payload, capture_output=True, text=True, timeout=120)
    line = out.stdout.strip().splitlines()
    if not line:
        return {"error": out.stderr.strip()[:300] or "no output"}
    try:
        return json.loads(line[-1])
    except Exception:
        return {"raw": line[-1][:300]}


def js(expr):
    return call("POST", "/evaluate", {"expression": expr})


def click_text(text):
    return js(f"""(function(){{
var b=[...document.querySelectorAll('button,[role=button],a')]
 .find(e=>(e.innerText||'').trim()==={json.dumps(text)});
if(b){{b.click();return 'clicked {text}'}}return 'not found: {text}'}})()""")


def main():
    print("[*] navigate ->", VERIFY_URL)
    print(call("POST", "/browse", {"url": VERIFY_URL}))

    # Poll until the policy/landing page renders its checkbox or Get started.
    deadline = time.time() + 30
    while time.time() < deadline:
        state = js("""JSON.stringify({
url:location.href,
hasCb:!!document.querySelector('[type=checkbox],[role=checkbox]'),
hasGS:[...document.querySelectorAll('button')].some(b=>(b.innerText||'').trim()==='Get started'),
hasUpload:[...document.querySelectorAll('button')].some(b=>(b.innerText||'').trim()==='Upload'),
hasFile:!!document.querySelector('input[type=file]')
})""").get("result", "{}")
        try:
            s = json.loads(state)
        except Exception:
            s = {}
        if s.get("hasCb") or s.get("hasGS") or s.get("hasUpload") or s.get("hasFile"):
            break
        time.sleep(3)
    print("[*] page state:", state[:300])

    if not (s.get("hasUpload") or s.get("hasFile")):
        # Policy/landing page: check the agreement checkbox then Get started.
        print("[*] checkbox:", js("""(function(){
var c=document.querySelector('[type=checkbox],[role=checkbox]');
if(c){c.click();return 'clicked checked='+(c.checked||c.getAttribute('aria-checked'))}
return 'no checkbox'})()""").get("result"))
        time.sleep(1)
        print("[*] get started:", click_text("Get started").get("result"))
        # Poll for the verify form (Upload button or file input).
        deadline = time.time() + 30
        while time.time() < deadline:
            s2 = js("""JSON.stringify({
url:location.href,
hasUpload:[...document.querySelectorAll('button')].some(b=>(b.innerText||'').trim()==='Upload'),
hasFile:!!document.querySelector('input[type=file]')
})""").get("result", "{}")
            try:
                s = json.loads(s2)
            except Exception:
                s = {}
            if s.get("hasUpload") or s.get("hasFile"):
                break
            time.sleep(3)
    print("[*] form url:", js("location.href").get("result"))

    # If the form is in edit mode already, click Upload on certification.
    print("[*] upload:", js("""(function(){
var bs=[...document.querySelectorAll('button')];
var b=bs.find(e=>(e.innerText||'').trim()==='Upload');
if(b){b.click();return 'clicked upload'}return 'upload not found'})()""").get("result"))
    time.sleep(4)

    # Inject cert via DataTransfer -> input.files -> change event.
    b64 = base64.b64encode(CERT.read_bytes()).decode()
    mime = "image/png" if CERT.suffix == ".png" else "application/pdf"
    fname = CERT.name
    expr = (
        "(async function(){"
        "var input=document.querySelector('input[type=file]');"
        "if(!input) return 'no file input';"
        f"var bin=atob('{b64}');"
        "var bytes=new Uint8Array(bin.length);"
        "for(var i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);"
        f"var f=new File([bytes],'{fname}',{{type:'{mime}'}});"
        "var dt=new DataTransfer(); dt.items.add(f); input.files=dt.files;"
        "input.dispatchEvent(new Event('change',{bubbles:true}));"
        "return 'injected '+f.name+' '+f.size+'b'})()"
    )
    print("[*] inject:", js(expr).get("result"))
    time.sleep(5)

    # Report what the form shows now (preview / filename / errors).
    print("[*] state:", js("""JSON.stringify({
text:document.body.innerText.slice(-1500),
fileInputs:[...document.querySelectorAll('input[type=file]')].length
})""").get("result", "")[:1600])

    # Open the company-info "Edit" section — that expands the whole form
    # (address, website, cert, name, license) and reveals the Submit button.
    print("[*] edit:", js("""(function(){
var bs=[...document.querySelectorAll('button')].filter(b=>(b.innerText||'').trim()==='Edit');
var b=bs[bs.length-1];
if(b){b.click();return 'clicked last Edit ('+bs.length+' total)'}
return 'no Edit button'})()""").get("result"))
    time.sleep(2)

    # Set company name + license number with React-compatible events.
    name_js = f"""(function(){{
var inps=[...document.querySelectorAll('input')];
var inp=inps.find(i=>(i.value||'').length>3&&!/^[0-9]+$/.test(i.value||''));
var cands=inps.filter(i=>(i.value||'').length>3);
if(cands.length<2)return 'inputs not ready: '+JSON.stringify(inps.map(i=>i.value));
var nameInp=cands[cands.length-2], licInp=cands[cands.length-1];
var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
setter.call(nameInp,{json.dumps(COMPANY_NAME)});
nameInp.dispatchEvent(new Event('input',{{bubbles:true}}));
nameInp.dispatchEvent(new Event('change',{{bubbles:true}}));
setter.call(licInp,{json.dumps(LICENSE_NUMBER)});
licInp.dispatchEvent(new Event('input',{{bubbles:true}}));
licInp.dispatchEvent(new Event('change',{{bubbles:true}}));
return 'name='+nameInp.value+' lic='+licInp.value}})()"""
    print("[*] fields:", js(name_js).get("result"))
    time.sleep(1)

    # Submit.
    print("[*] submit:", js("""(async function(){
var sub=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='Submit');
if(!sub)return 'no Submit';
sub.scrollIntoView();sub.click();
await new Promise(r=>setTimeout(r,4000));
var t=document.body.innerText;
return t.includes('Under Review')?'UNDER REVIEW':(t.includes('Disapproved')?'DISAPPROVED':'submitted? '+t.slice(-200))})()""").get("result"))


if __name__ == "__main__":
    main()
