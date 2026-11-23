#!/usr/bin/env python3
"""meta_recovery_codes.py — generate fresh Meta Accounts Center recovery
codes over CDP on the debug Chrome (:9229) and store them canonically.

Battle-tested live 2026-11-23: the full gate chain is
  accountscenter 2FA -> "Manage authentication" -> email confirmation code
  (self-read via gmail_code_fetch.py) -> password re-entry (FACEBOOK_PASSWORD)
  -> Recovery codes -> "Get new codes" -> scrape -> cred_set META_BACKUP_CODES.

IMPORTANT scope note: these are *Meta Account* recovery codes. An
Instagram profile with its own legacy 2FA may reject them — verify on the
IG challenge before assuming they work (observed: cloudless.gr IG rejected
fresh Meta-account codes AND both stored TOTP seeds — its 2FA is enrolled
separately; SMS/WhatsApp to +**38 also never arrives => Meta support case).

Usage:
  meta_recovery_codes.py --status     # is META_BACKUP_CODES stored + count
  meta_recovery_codes.py              # full drive (needs live FB session)
  meta_recovery_codes.py --no-store   # generate + print count only

Env: CDP_URL (default http://192.168.96.1:9229), GMC (gmail code fetch cmd).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

CDP = os.environ.get("CDP_URL", "http://192.168.96.1:9229")
TWOFA = "https://accountscenter.facebook.com/password_and_security/two_factor"
CRED_SET = os.path.expanduser(
    "~/cu130-slim/.devin/skills/platform-cred-store-ops/scripts/cred_set.py")
GMAIL_FETCH = os.path.expanduser(
    "~/cu130-slim/.devin/skills/mail-code-fetch/scripts/gmail_code_fetch.py")
ENV = os.path.expanduser("~/cu130-slim/.env")
WS_PY = os.environ.get(
    "CDP_PY", os.path.expanduser("~/.venv-igdrive/bin/python3"))


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list",
                                             timeout=10).read())


def eval_js(tab_id, expr, ws_url=None):
    """Run Runtime.evaluate via a small websockets helper subprocess."""
    helper = "/tmp/_mrc_eval.py"
    with open(helper, "w") as f:
        f.write("import json,sys\nfrom websockets.sync.client import connect\n"
                "ws=connect(sys.argv[1],open_timeout=20)\n"
                "ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':"
                "{'expression':sys.argv[2],'returnByValue':True,'awaitPromise':True}}))\n"
                "r=json.loads(ws.recv())\n"
                "print(json.dumps(r.get('result',{}).get('result',{})))\n")
    out = subprocess.run([WS_PY, helper, ws_url, expr],
                         capture_output=True, text=True, timeout=60)
    try:
        return json.loads(json.loads(out.stdout)["value"])
    except Exception:
        return {"_raw": out.stdout[-300:], "_err": out.stderr[-300:]}


def find_tab(sub):
    for t in tabs():
        if t.get("type") == "page" and sub in t.get("url", ""):
            return t
    return None


def nav(tab, url):
    t = find_tab(url.split("/")[2]) or tab
    # Page.navigate via helper
    helper = "/tmp/_mrc_nav.py"
    with open(helper, "w") as f:
        f.write("import json,sys\nfrom websockets.sync.client import connect\n"
                "ws=connect(sys.argv[1],open_timeout=20)\n"
                "ws.send(json.dumps({'id':1,'method':'Page.navigate','params':{'url':sys.argv[2]}}))\n"
                "ws.recv()\n")
    subprocess.run([WS_PY, helper, t["webSocketDebuggerUrl"], url],
                   capture_output=True, timeout=30)
    time.sleep(6)
    return find_tab("accountscenter") or t


def click_text(t, pattern, max_children=8):
    js = ("(()=>{const els=[...document.querySelectorAll("
          "'a,div[role=link],div[role=button],button,div,span')];"
          "const t=els.filter(e=>new RegExp('%s','i').test((e.innerText||'').trim())"
          "&&(e.innerText||'').trim().length<60&&e.children.length<%d).pop();"
          "if(!t)return 'NOTFOUND';t.click();return 'CLICKED '+(t.innerText||'').trim().slice(0,50)})()"
          % (pattern, max_children))
    return eval_js(t["id"], js, t["webSocketDebuggerUrl"])


def body_text(t):
    return eval_js(t["id"],
                   "document.body?document.body.innerText.slice(0,4000):''",
                   t["webSocketDebuggerUrl"]) or ""


def fb_password():
    for line in open(ENV):
        if line.startswith("FACEBOOK_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def get_email_code(t):
    """Poll Gmail for the Meta confirmation code (Windows python path)."""
    win = subprocess.run(["wslpath", "-w", GMAIL_FETCH],
                         capture_output=True, text=True).stdout.strip()
    py = r"C:\Program Files\Python312\python.exe"
    for _ in range(12):
        r = subprocess.run(
            ["/mnt/c/Program Files/Python312/python.exe", win,
             "--from", "facebookmail.com", "--pattern", "([0-9]{6})",
             "--timeout", "25"],
            capture_output=True, text=True, timeout=60)
        code = (r.stdout or "").strip().splitlines()
        if code and re.fullmatch(r"\d{6}", code[-1]):
            return code[-1]
        time.sleep(5)
    return None


def fill_input(t, selector_js, value):
    js = ("(()=>{const inp=%s;if(!inp)return 'NOINPUT';"
          "const set=Object.getOwnPropertyDescriptor("
          "window.HTMLInputElement.prototype,'value').set;"
          "set.call(inp,%s);inp.dispatchEvent(new Event('input',{bubbles:true}));"
          "inp.dispatchEvent(new Event('change',{bubbles:true}));return 'FILLED'})()"
          % (selector_js, json.dumps(value)))
    return eval_js(t["id"], js, t["webSocketDebuggerUrl"])


def click_btn(t, label):
    js = ("(()=>{const els=[...document.querySelectorAll("
          "'div[role=button],button')];const b=els.find(e=>/^%s$/i.test("
          "(e.innerText||'').trim()));if(!b)return 'NOBTN';b.click();return 'OK'})()"
          % label)
    return eval_js(t["id"], js, t["webSocketDebuggerUrl"])


def status():
    for path in (ENV, os.path.expanduser("~/cu130-slim/deploy/"
                                         "socialauto-standalone/.env")):
        if os.path.exists(path):
            for line in open(path):
                if line.startswith("META_BACKUP_CODES="):
                    try:
                        codes = json.loads(line.split("=", 1)[1].strip())
                        print(f"{path}: {len(codes)} codes stored")
                    except Exception:
                        print(f"{path}: present (unparsed)")
                    return
    print("META_BACKUP_CODES: NOT SET")


def main():
    if "--status" in sys.argv:
        status(); return 0
    t = find_tab("facebook.com") or find_tab("accountscenter")
    if not t:
        print("no FB/accountscenter tab on", CDP); return 2
    t = nav(t, TWOFA)
    time.sleep(2)
    txt = body_text(t)
    if "Manage authentication" in txt:
        print(click_text(t, "manage authentication")); time.sleep(5)
        t = find_tab("accountscenter")
        txt = body_text(t)
    if "confirmation code" in txt.lower() or "Authenticity" in txt:
        code = get_email_code(t)
        print("email code:", "got" if code else "TIMEOUT")
        if not code:
            return 3
        fill_input(t, "document.querySelector('input')", code)
        click_btn(t, "Next"); time.sleep(6)
        t = find_tab("accountscenter")
    if "re-enter your password" in body_text(t).lower():
        pw = fb_password()
        fill_input(t, "document.querySelector('input[type=password]')||"
                      "document.querySelector('input')", pw or "")
        click_btn(t, "Continue"); time.sleep(8)
        t = find_tab("accountscenter")
    # navigate to recovery codes section
    if "Recovery codes" in body_text(t) and "Get new codes" not in body_text(t):
        print(click_text(t, "recovery codes")); time.sleep(5)
        t = find_tab("accountscenter")
    if "Get new codes" not in body_text(t):
        print("codes screen not reached; text:", body_text(t)[:400])
        return 4
    print(click_text(t, "get new codes")); time.sleep(6)
    t = find_tab("accountscenter")
    txt = body_text(t)
    codes = re.findall(r"\b(\d{4}) (\d{4})\b", txt)
    flat = [a + b for a, b in codes]
    print(f"codes captured: {len(flat)}")
    if not flat:
        return 5
    if "--no-store" not in sys.argv:
        with open(os.path.expanduser("~/meta_backup_codes.txt"), "w") as f:
            f.write("\n".join(flat) + "\n")
        os.chmod(os.path.expanduser("~/meta_backup_codes.txt"), 0o600)
        p = subprocess.run(["python3", CRED_SET, "META_BACKUP_CODES",
                            "--value-stdin"], input=json.dumps(flat),
                           text=True, capture_output=True, timeout=90)
        print("cred_set:", "ok" if p.returncode == 0 else p.stderr[-200:])
    print("sha:", __import__("hashlib").sha256(
        "".join(flat).encode()).hexdigest()[:10])
    return 0


if __name__ == "__main__":
    sys.exit(main())
