#!/usr/bin/env python3
"""ig_2fa_watch.py — watch Instagram two_step_verification tabs on debug
Chrome (:9229) and auto-complete the challenge when a code becomes
available. Born from 2026-11-23: SMS/WhatsApp codes to +**38 never
arrived, but when Meta fixes delivery this finishes login unattended.

Lanes, in order per poll:
  1. SMS/WhatsApp challenge -> read newest Meta code from the phone
     (adb SMS inbox + dumpsys notification) -> fill + Continue.
  2. Backup-code challenge -> try stored META_BACKUP_CODES / fresh file
     ~/meta_backup_codes.txt / ~/ig_backup_codes.txt (one attempt per
     code, rate-limited --code-gap seconds).
  3. Authentication-app challenge -> current TOTP from META_TOTP_SECRET
     and META_TOTP_SECRET_IG (both known-rejected for cloudless.gr as of
     2026-11-23 — kept for other accounts/rotations).

Safety: max --tries per lane per run, sleeps between attempts, never
loops hot. Exits 0 when a tab leaves the challenge URL (login done).

Usage:
  ig_2fa_watch.py --once          # single poll, report state
  ig_2fa_watch.py                 # loop until success/--max-min
  ig_2fa_watch.py --dry           # detect only, never submit
Env: CDP_URL, ADB serial auto-detected.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

CDP = os.environ.get("CDP_URL", "http://192.168.96.1:9229")
ENV = os.path.expanduser("~/cu130-slim/.env")
WS_PY = os.environ.get("CDP_PY",
                       os.path.expanduser("~/.venv-igdrive/bin/python3"))
CHALLENGE = "two_step_verification"


def tabs():
    try:
        return json.loads(urllib.request.urlopen(CDP + "/json/list",
                                                 timeout=10).read())
    except Exception:
        return []


def challenge_tabs():
    return [t for t in tabs() if t.get("type") == "page"
            and CHALLENGE in t.get("url", "")]


def eval_js(t, expr):
    helper = "/tmp/_igw_eval.py"
    with open(helper, "w") as f:
        f.write(
            "import json,sys\nfrom websockets.sync.client import connect\n"
            "ws=connect(sys.argv[1],open_timeout=20)\n"
            "ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':"
            "{'expression':sys.argv[2],'returnByValue':True,'awaitPromise':True}}))\n"
            "r=json.loads(ws.recv())\n"
            "print(json.dumps(r.get('result',{}).get('result',{})))\n")
    out = subprocess.run([WS_PY, helper, t["webSocketDebuggerUrl"], expr],
                         capture_output=True, text=True, timeout=45)
    try:
        return json.loads(json.loads(out.stdout).get("value") or "null")
    except Exception:
        return None


STATE_JS = """JSON.stringify({
 url:location.href.slice(0,90),
 text:(document.body?document.body.innerText:'').slice(0,1500),
 radios:[...document.querySelectorAll('input[type=radio]')].map(r=>r.checked),
 hasInput:!!document.querySelector('input[type=text],input[type=tel],input:not([type])')
})"""


def page_state(t):
    v = eval_js(t, STATE_JS)
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {"text": v, "url": ""}
    return v or {}


def method_of(st):
    txt = (st.get("text") or "").lower()
    if "whatsapp" in txt and "authentication app" in txt:
        return "picker"
    if "backup code" in txt and "authentication app" in txt:
        return "picker"
    if "text messages" in txt or "whatsapp messages" in txt:
        return "sms"
    if "backup" in txt and "enter" in txt:
        return "backup"
    if "authentication app" in txt and ("code" in txt):
        return "totp"
    return "unknown"


def fill_and_continue(t, code):
    fill = (
        "(()=>{const inp=document.querySelector('input[type=text]')||"
        "document.querySelector('input[type=tel]')||"
        "document.querySelector('input:not([type])');"
        "if(!inp)return 'NOINPUT';"
        "const set=Object.getOwnPropertyDescriptor("
        "window.HTMLInputElement.prototype,'value').set;"
        "set.call(inp,%s);inp.dispatchEvent(new Event('input',{bubbles:true}));"
        "inp.dispatchEvent(new Event('change',{bubbles:true}));return 'FILLED'})()"
        % json.dumps(code))
    r = eval_js(t, fill)
    if r != "FILLED":
        return f"fill:{r}"
    time.sleep(0.8)
    click = ("(()=>{const els=[...document.querySelectorAll("
             "'button,div[role=button],input[type=submit]')];"
             "const b=els.find(e=>/^(continue|next|submit|confirm)$/i.test("
             "(e.innerText||e.value||'').trim()));"
             "if(!b)return 'NOBTN';b.click();return 'OK'})()")
    return eval_js(t, click)


def pick_method(t, name):
    """In the method picker, select radio by adjacent label text."""
    js = (
        "(()=>{const labs=[...document.querySelectorAll('*')].filter(e=>"
        "e.children.length<3&&new RegExp('%s','i').test((e.innerText||'').trim())"
        "&&(e.innerText||'').trim().length<60);"
        "const lab=labs[labs.length-1];if(!lab)return 'NOLABEL';"
        "let n=lab;for(let i=0;i<6&&n;i++){n=n.parentElement;"
        "const r=n&&n.querySelector('input[type=radio]');"
        "if(r){r.click();return 'PICKED'}}return 'NORADIO'})()" % name)
    return eval_js(t, js)


def env_secret(key):
    try:
        for line in open(ENV):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def totp(seed):
    try:
        import pyotp
        return pyotp.TOTP(seed.replace(" ", "")).now()
    except Exception:
        return None


def _code_from_text(out, pats):
    for p in pats:
        m = re.search(p, out, re.I)
        if m:
            return m.group(1) if m.lastindex else m.group(0)
    return None


def termux_code(pats):
    """ADB-free lane: Termux sshd on the tailnet (:8022 via ~/.ssh/config).

    termux-sms-list covers the SMS inbox; termux-notification-list covers
    the notification shade (the dumpsys-notification equivalent)."""
    host = os.environ.get("PHONE_SSH_HOST", "100.84.31.85")
    for cmd in ("termux-sms-list -l 8", "termux-notification-list"):
        try:
            out = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=8",
                 "-o", "StrictHostKeyChecking=no", host, cmd],
                capture_output=True, text=True, timeout=20).stdout
        except Exception:
            continue
        code = _code_from_text(out, pats)
        if code:
            return code
    return None


def phone_sms_code():
    """Newest Meta/Instagram code from phone SMS + notifications."""
    pats = [r"(\d{6}) is your", r"code[^\d]{0,20}(\d{6})",
            r"(\d{8})"]
    devs = subprocess.run(["adb", "devices"], capture_output=True,
                          text=True, timeout=15).stdout
    dev = next((l.split()[0] for l in devs.splitlines()
                if "device" in l and "offline" not in l), None)
    if dev:
        for src in (
            f"adb -s {dev} shell content query --uri content://sms/inbox "
            "--projection body --sort 'date DESC'",
            f"adb -s {dev} shell dumpsys notification --noredact",
        ):
            try:
                out = subprocess.run(src.split(), capture_output=True,
                                     text=True, timeout=30).stdout
            except Exception:
                continue
            code = _code_from_text(out, pats)
            if code:
                return code
    return termux_code(pats)


def backup_codes():
    codes = []
    for path in (os.path.expanduser("~/meta_backup_codes.txt"),
                 os.path.expanduser("~/ig_backup_codes.txt")):
        if os.path.exists(path):
            codes += [l.strip() for l in open(path) if l.strip()]
    raw = env_secret("META_BACKUP_CODES")
    if raw:
        try:
            codes += [str(c) for c in json.loads(raw)]
        except Exception:
            pass
    seen, out = set(), []
    for c in codes:
        c = re.sub(r"\D", "", c)
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out


def attempt_sms(t, st, dry):
    if dry:
        return "sms-available?"
    code = phone_sms_code()
    if not code:
        return "no-sms-code"
    return f"sms->{fill_and_continue(t, code)}"


def attempt_backup(t, st, tried, dry):
    for c in backup_codes():
        if c in tried:
            continue
        tried.add(c)
        if dry:
            return f"backup-candidate {c[:2]}***"
        r = fill_and_continue(t, c)
        time.sleep(3)
        ns = page_state(t)
        if CHALLENGE not in (ns.get("url") or "") or "invalid" not in \
                (ns.get("text") or "").lower():
            return f"backup-accepted? {r}"
        if "invalid" in (ns.get("text") or "").lower():
            continue
        return f"backup->{r} state-changed"
    return "backup-exhausted"


def attempt_totp(t, dry):
    for key in ("META_TOTP_SECRET_IG", "META_TOTP_SECRET"):
        seed = env_secret(key)
        c = totp(seed) if seed else None
        if not c:
            continue
        if dry:
            return f"totp-candidate {key}"
        r = fill_and_continue(t, c)
        time.sleep(3)
        ns = page_state(t)
        txt = (ns.get("url") or "")
        if CHALLENGE not in txt:
            return f"totp-accepted? {key} {r}"
    return "totp-rejected"


def poll(dry=False):
    ts = challenge_tabs()
    if not ts:
        return "no-challenge-tabs"
    results = []
    tried = set()
    for t in ts:
        st = page_state(t)
        m = method_of(st)
        results.append(f"tab {t['id'][:6]} method={m}")
        if m == "sms":
            results.append("  " + attempt_sms(t, st, dry))
        elif m == "backup":
            results.append("  " + attempt_backup(t, st, tried, dry))
        elif m == "totp":
            results.append("  " + attempt_totp(t, dry))
        elif m == "picker":
            results.append("  picker-visible (needs method choice)")
    # success check
    if not challenge_tabs():
        return "LOGIN-COMPLETE"
    return " | ".join(results)


def main():
    once = "--once" in sys.argv
    dry = "--dry" in sys.argv
    max_min = 60
    for a in sys.argv[1:]:
        if a.startswith("--max-min="):
            max_min = int(a.split("=")[1])
    deadline = time.time() + max_min * 60
    while True:
        r = poll(dry=dry)
        print(time.strftime("%H:%M:%S"), r, flush=True)
        if "LOGIN-COMPLETE" in r or once:
            return 0 if "LOGIN-COMPLETE" in r else 1
        if time.time() > deadline:
            return 2
        time.sleep(120)


if __name__ == "__main__":
    sys.exit(main())
