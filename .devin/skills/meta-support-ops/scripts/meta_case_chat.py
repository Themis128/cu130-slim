#!/usr/bin/env python3
"""meta_case_chat.py — drive an existing Meta Business Support case chat over CDP.

The contact-support wizard (fb_escalate.py) files cases; THIS tool talks to the
specialist/AI threads afterwards: list cases, open the chat thread, read the
transcript, send replies, and optionally watch for new messages.

Runs anywhere with `websocket-client` (stdlib + ws only) — WSL hits the debug
Chrome on the Windows gateway, Windows hits localhost.

Usage:
  meta_case_chat.py cases                      # list support cases + status
  meta_case_chat.py threads                    # list chat threads (maiba)
  meta_case_chat.py read [substr]              # dump tail of a thread (default: newest)
  meta_case_chat.py send "text" [substr]       # send into a thread, then print reply tail
  meta_case_chat.py send "text" --wait 120     # send + poll up to N sec for a reply
  meta_case_chat.py watch [substr] [--interval 300]  # poll for new msgs, TG-alert

Env: CDP_URL overrides candidate list. State for watch: ~/logs/meta-case-watch.json
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

try:
    import websocket
except ImportError:
    sys.exit("needs websocket-client: pip install websocket-client")

WSL_HOST_IP = os.environ.get("WSL_HOST_IP") or subprocess.run(
    ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
    capture_output=True, text=True, timeout=5).stdout.strip() or "192.168.96.1"

CDP_CANDIDATES = [os.environ["CDP_URL"]] if os.environ.get("CDP_URL") else [
    f"http://{WSL_HOST_IP}:9229", "http://localhost:9229",
    "http://localhost:9330", f"http://{WSL_HOST_IP}:9330"]

SUPPORT_URL = "https://www.facebook.com/business-support-home/"
STATE = os.path.expanduser("~/logs/meta-case-watch.json")


# ---------- CDP plumbing ----------

def _cdp_base():
    for base in CDP_CANDIDATES:
        try:
            tabs = json.load(urllib.request.urlopen(base + "/json/list", timeout=4))
            if tabs:
                return base
        except Exception:
            continue
    sys.exit("no CDP endpoint reachable: " + ", ".join(CDP_CANDIDATES))


class Tab:
    def __init__(self, base, match=None, create=False):
        self.base = base.rstrip("/")
        tabs = json.load(urllib.request.urlopen(self.base + "/json/list", timeout=5))
        pages = [t for t in tabs if t.get("type") == "page"]
        hit = next((t for t in pages if match and match in t.get("url", "")), None)
        if hit is None and create:
            req = urllib.request.Request(
                self.base + "/json/new?about:blank", method="PUT")
            hit = json.load(urllib.request.urlopen(req, timeout=5))
        if hit is None and pages:
            hit = pages[0]
        if hit is None:
            sys.exit("no page target on " + self.base)
        self.id = hit["id"]
        self._connect()
        self._mid = 0

    def _connect(self):
        tabs = json.load(urllib.request.urlopen(self.base + "/json/list", timeout=5))
        t = next((x for x in tabs if x.get("id") == self.id), None)
        if t is None:
            raise RuntimeError("target " + self.id + " gone")
        self.ws = websocket.create_connection(
            t["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)

    def cmd(self, method, params=None):
        for attempt in range(3):
            try:
                return self._cmd_once(method, params)
            except websocket.WebSocketException:
                if attempt == 2:
                    raise
                try:
                    self.ws.close()
                except Exception:
                    pass
                time.sleep(1)
                self._connect()

    def _cmd_once(self, method, params):
        self._mid += 1
        self.ws.send(json.dumps(
            {"id": self._mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._mid:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    def eval(self, expr, await_promise=False):
        r = self.cmd("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": await_promise})
        res = r.get("result", {})
        if res.get("subtype") == "error" or "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r.get("exceptionDetails", res))[:400])
        return res.get("value")

    def nav(self, url, settle=7):
        self.cmd("Page.enable")
        self.cmd("Page.navigate", {"url": url})
        time.sleep(settle)

    def click_xy(self, x, y):
        """Real mouse click — FB ignores el.click() on auth surfaces."""
        for t in ("mousePressed", "mouseReleased"):
            self.cmd("Input.dispatchMouseEvent",
                     {"type": t, "x": x, "y": y, "button": "left",
                      "clickCount": 1})

    def click_text(self, patterns, tags="[role=button],a,div[tabindex],span"):
        """Find first visible element whose text matches and real-click it.
        patterns: list of regex strings (case-insensitive). Returns matched text."""
        js = """(()=>{
          const pats=%s;
          const els=[...document.querySelectorAll(%s)];
          for(const e of els){
            const t=(e.innerText||'').replace(/\\s+/g,' ').trim();
            if(!t||t.length>90) continue;
            const r=e.getBoundingClientRect();
            if(r.width<5||r.height<5) continue;
            for(const p of pats){ if(new RegExp(p,'i').test(t)){
              return {x:r.x+r.width/2,y:r.y+r.height/2,txt:t.slice(0,60)}; }}
          } return null})()""" % (json.dumps(patterns), json.dumps(tags))
        hit = self.eval(js)
        if not hit:
            return None
        self.click_xy(hit["x"], hit["y"])
        return hit["txt"]

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ---------- page helpers (JS strings) ----------

JS_BODY = "document.body ? document.body.innerText : ''"

JS_THREADS = """(()=>{
  const out=[];
  document.querySelectorAll('[data-surface*="maiba:thread_list_item:"]').forEach(e=>{
    const id=(e.getAttribute('data-surface').match(/thread_list_item:(\\d+)/)||[])[1];
    const txt=(e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,120);
    out.push({id, txt});
  });
  return out})()"""

JS_CHAT_TEXT = """(()=>{
  // grab the chat pane: prefer the surface containing message bubbles
  const panes=[...document.querySelectorAll('div')].filter(d=>{
    const t=d.innerText||'';
    return t.length>200 && /Add a reply|Send message|Scroll to bottom/.test(t)
      && d.querySelector('[contenteditable],textarea');
  });
  const pane=panes[panes.length-1]||document.body;
  return (pane.innerText||'').replace(/\\n{3,}/g,'\\n\\n').slice(-6000)})()"""

JS_FIND_REPLY_BOX = """(()=>{
  const cands=[...document.querySelectorAll('[contenteditable="true"],textarea,[role=textbox],input[type=text]')];
  const vis=cands.filter(e=>{const r=e.getBoundingClientRect();return r.width>50&&r.height>10});
  const hint=e=>((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('placeholder')||'')+' '+(e.innerText||'').slice(0,60));
  const box=vis.find(e=>/reply|message|ask|write/i.test(hint(e)))||vis[vis.length-1];
  if(!box) return null;
  box.scrollIntoView({block:'center'}); box.focus();
  return {tag:box.tagName, aria:box.getAttribute('aria-label'), ph:box.getAttribute('placeholder'), ce:box.getAttribute('contenteditable')};
})()"""

JS_TYPE_AND_SEND = """(()=>{
  const TEXT = __TEXT__;
  const cands=[...document.querySelectorAll('[contenteditable="true"],textarea,[role=textbox],input[type=text]')];
  const vis=cands.filter(e=>{const r=e.getBoundingClientRect();return r.width>50&&r.height>10});
  const hint=e=>((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('placeholder')||'')+' '+(e.innerText||'').slice(0,60));
  const box=vis.find(e=>/reply|message|ask|write/i.test(hint(e)))||vis[vis.length-1];
  if(!box) return 'no-box';
  box.focus();
  if(box.tagName==='TEXTAREA'||box.tagName==='INPUT'){
    const p=Object.getPrototypeOf(box);
    Object.getOwnPropertyDescriptor(p,'value').set.call(box,TEXT);
    box.dispatchEvent(new Event('input',{bubbles:true}));
    box.dispatchEvent(new Event('change',{bubbles:true}));
  }else{
    box.innerText=TEXT;
    box.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:TEXT}));
  }
  return 'typed'})()"""

JS_CLICK_SEND = """(()=>{
  const b=document.querySelector('[aria-label*="Send message" i],[aria-label*="send" i][role=button]')
        ||[...document.querySelectorAll('[role=button]')].find(e=>/send/i.test(e.getAttribute('aria-label')||''));
  if(b){b.click(); return 'sent-aria'}
  const ae=document.activeElement;
  if(ae){ae.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
        ae.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
        return 'sent-enter'}
  return 'no-send-btn'})()"""

JS_CHAT_UNAVAILABLE = """(()=>/Customer Support Chat Isn't Available/i.test(document.body.innerText||''))()"""

JS_CASES = """(()=>{
  const out=[];
  document.querySelectorAll('[data-surface*="support_case_item:"]').forEach(e=>{
    const surf=e.getAttribute('data-surface');
    const id=(surf.match(/support_case_item:(\\d+)/)||[])[1];
    if(id && !out.find(x=>x.id===id)){
      // walk up to the row container for title/status text
      let row=e; for(let i=0;i<6&&row.parentElement;i++){row=row.parentElement;
        if((row.innerText||'').length>40) break;}
      out.push({id, row:(row.innerText||'').replace(/\\s+/g,' ').trim().slice(0,300)});
    }
  });
  return out})()"""


PICKER_MARKERS = ("Use another profile", "Create new account")


def _on_login_wall(tab):
    url = tab.eval("location.href") or ""
    return "login" in url or "recover" in url or "checkpoint" in url


def handle_profile_picker(tab):
    """Real-click 'Continue' on the FB logged-out profile picker.
    el.click() is ignored on this surface — needs Input.dispatchMouseEvent."""
    hit = tab.click_text(["^continue$"])
    if not hit:
        return "stuck"
    time.sleep(6)
    url = tab.eval("location.href") or ""
    if "recover" in url:
        return "recover"
    if "login" in url or "checkpoint" in url:
        return "login-wall"
    return "advanced"


def ensure_support_page(tab, settle=8):
    """Navigate to support home, clearing the FB profile-picker gate."""
    tab.nav(SUPPORT_URL, settle=settle)
    for _ in range(5):
        url = tab.eval("location.href") or ""
        if "login" in url:
            return "NEEDS-REAUTH"
        body = tab.eval(JS_BODY) or ""
        is_picker = ("Use another profile" in body or
                     ("Themistoklis Baltzakis" in body and
                      "Create new account" in body))
        if is_picker:
            r = handle_profile_picker(tab)
            if r == "login-wall":
                return "NEEDS-REAUTH"
            if r == "recover":
                return "NEEDS-PASSWORD"
            time.sleep(3)
            if "business-support" not in (tab.eval("location.href") or ""):
                tab.nav(SUPPORT_URL, settle=6)
            continue
        if tab.eval(JS_CHAT_UNAVAILABLE):
            time.sleep(4)
        return "ok"
    return "gate-loop"


def _env_val(key):
    for p in ("/home/tbaltzakis/cu130-slim/.env",
              "/mnt/c/Users/tbaltzakis/cu130-slim/.env"):
        try:
            for line in open(p):
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return None


def _fill_input(tab, selector, value):
    js = """(()=>{
      const i=document.querySelector(%s);
      if(!i) return 'no-input';
      i.focus();
      const p=Object.getPrototypeOf(i);
      Object.getOwnPropertyDescriptor(p,'value').set.call(i,%s);
      i.dispatchEvent(new Event('input',{bubbles:true}));
      i.dispatchEvent(new Event('change',{bubbles:true}));
      return i.value.length})()""" % (json.dumps(selector), json.dumps(value))
    return tab.eval(js)


def _store_val(key):
    """Canonical secret-store read (SocialAuto API). None on any failure."""
    try:
        sys.path.insert(0, "/home/tbaltzakis/cu130-slim/.devin/skills/_lib")
        from skill_http import social_api, request_status
        api, tok = social_api()
        c, b = request_status("GET", f"{api}/api/v1/secrets/{key}", token=tok)
        if c == 200:
            return json.loads(b).get("value", "") or None
    except Exception:
        pass
    return None


def _fb_password():
    """Store is canonical — .env FACEBOOK_PASSWORD drifted stale (2026-11-23:
    store=16-char Meta Account password, .env=20-char legacy)."""
    return (_store_val("FACEBOOK_PASSWORD") or _env_val("FACEBOOK_PASSWORD")
            or _env_val("FB_PASSWORD"))


def _form_log_in(tab):
    """Click the Log in button INSIDE the form holding the password field —
    the footer 'Log In' link matches the same regex and does nothing."""
    hit = tab.eval("""(()=>{const i=document.querySelector('input[type=password]');if(!i)return null;
     const form=i.closest('form');const btn=[...(form?form:document).querySelectorAll('div[role=button],button')]
      .find(e=>/^log in$/i.test((e.innerText||'').trim()));if(!btn)return null;
     const r=btn.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
    if hit:
        tab.click_xy(hit["x"], hit["y"])
        return True
    return False


def fb_password_login(tab):
    """Drive facebook.com Meta Account login end-to-end.
    picker -> recover/chooser (password radio) -> password form -> 2FA.
    Credentials from the canonical secret store — never printed.
    Returns 'ok' | 'needs-phone-approval' | 'needs-2fa-code' | 'failed:...'"""
    tab.nav("https://www.facebook.com/", settle=7)
    tab.click_text(["allow all cookies"])
    time.sleep(1)
    body = tab.eval(JS_BODY) or ""
    # 1) logged-out profile picker -> Continue (real click)
    if "Use another profile" in body or "Create new account" in body:
        handle_profile_picker(tab)
        time.sleep(4)
        body = tab.eval(JS_BODY) or ""
    url = tab.eval("location.href") or ""
    # 2) 'Choose a way to log in' chooser -> password radio + Continue
    if "recover" in url or "Choose a way to log in" in body:
        hit = tab.eval("""(()=>{const g=document.querySelector('[role=radiogroup]');
         if(!g)return null;const r=g.getBoundingClientRect();
         return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        if hit:
            tab.click_xy(hit["x"], hit["y"])
            time.sleep(2)
        tab.click_text(["^continue$"])
        time.sleep(5)
        body = tab.eval(JS_BODY) or ""
        url = tab.eval("location.href") or ""
    # 3) password form — Meta Account inline form OR recover form
    pw = _fb_password()
    if not pw:
        return "failed:no-facebook-password"
    has_pw = tab.eval("!!document.querySelector('input[type=password]')")
    if has_pw or "login" in url or "recover" in url or "Log into your Meta Account" in body:
        # ensure the form is the visible one; fill password only (account preselected)
        _fill_input(tab, "input[type=password]", pw)
        time.sleep(1)
        _form_log_in(tab)
        time.sleep(10)
        body = tab.eval(JS_BODY) or ""
        url = tab.eval("location.href") or ""
    # 4) classify outcome
    low = body.lower()
    if "was this you" in low or "approve the login" in low or \
            "waiting for approval" in low:
        return "needs-phone-approval"
    if "two-factor" in low or "authentication app" in low or \
            "backup code" in low or "enter the code" in low or \
            "security code" in low or "two_step_verification" in url:
        return "needs-2fa-code"
    if "checkpoint" in url:
        return "checkpoint:" + url[:110]
    if "password you entered is incorrect" in low or "password is invalid" in low:
        return "failed:password-rejected"
    if not tab.eval("!!document.querySelector('input[type=password]')") and \
            "login" not in url and "recover" not in url:
        return "ok"
    return ("unknown:" + url[:100] + " | "
            + re.sub(r"\s+", " ", body)[:200])


JS_OPEN_CASE_CHAT = """(()=>{
  const b=document.querySelector('[data-surface*="account_quality_hub_support_case_item_button_open"]');
  if(b){b.click(); return b.getAttribute('data-surface');} return null})()"""


def ensure_or_login(tab):
    """ensure_support_page + one automatic password-login retry."""
    st = ensure_support_page(tab)
    if st == "NEEDS-PASSWORD":
        print("session gate: running fb_password_login…", file=sys.stderr)
        res = fb_password_login(tab)
        print("login result:", res, file=sys.stderr)
        if res == "ok":
            st = ensure_support_page(tab, settle=6)
        else:
            return res
    return st


def open_thread(tab, substr=None, wait=6):
    """Click a maiba thread_list_item; returns thread id or None.
    If no threads are listed yet, opens the case's 'Go to chat' pane first."""
    have = tab.eval("document.querySelectorAll('[data-surface*=\"maiba:thread_list_item:\"]').length")
    if not have:
        surf = tab.eval(JS_OPEN_CASE_CHAT)
        if surf:
            print("opened case chat:", surf, file=sys.stderr)
            time.sleep(wait + 2)
    js = """(()=>{
      const items=[...document.querySelectorAll('[data-surface*="maiba:thread_list_item:"]')];
      const want=__WANT__;
      const pick = want ? items.find(e=>(e.innerText||'').toLowerCase().includes(want)) : items[0];
      if(!pick) return null;
      pick.click();
      return (pick.getAttribute('data-surface').match(/thread_list_item:(\\d+)/)||[])[1]})()""" \
        .replace("__WANT__", json.dumps((substr or "").lower()))
    tid = tab.eval(js)
    if tid:
        time.sleep(wait)
    return tid


def cmd_login2fa(tab):
    """On a two_step_verification page: Try another way -> Authentication app
    -> enter TOTP from META_TOTP_SECRET_AC (store) -> submit."""
    import pyotp
    secret = _store_val("META_TOTP_SECRET_AC") or _env_val("META_TOTP_SECRET_AC")
    if not secret:
        print("no META_TOTP_SECRET_AC in store/env")
        return
    url = tab.eval("location.href") or ""
    body = tab.eval(JS_BODY) or ""
    if "two_step_verification" not in url and "authentication app" not in body.lower():
        print("not on a 2FA page:", url[:90])
        return
    if "try another way" in body.lower():
        tab.click_text(["try another way"])
        time.sleep(4)
    # pick 'Authentication app' row then Continue
    hit = tab.eval("""(()=>{
     const els=[...document.querySelectorAll('div[role=button],label,[role=radio],div[tabindex],li')];
     for(const e of els){const t=(e.innerText||'').replace(/\\s+/g,' ').trim();
      if(/authentication app/i.test(t)&&t.length<120){const r=e.getBoundingClientRect();
       if(r.width>50&&r.height>10) return {x:r.x+r.width/2,y:r.y+r.height/2};}}
     return null})()""")
    if hit:
        tab.click_xy(hit["x"], hit["y"])
        time.sleep(3)
    tab.click_text(["^continue$"])
    time.sleep(5)
    # code entry — 6-digit input(s)
    code = pyotp.TOTP(secret).now()
    filled = tab.eval("""((code)=>{
     const ins=[...document.querySelectorAll('input[inputmode=numeric],input[autocomplete=one-time-code],input[type=tel],input[type=text],input[type=number]')]
      .filter(i=>{const r=i.getBoundingClientRect();return r.width>5});
     if(!ins.length) return 'no-inputs';
     if(ins.length>=6){ // per-digit boxes
       ins.slice(0,6).forEach((el,i)=>{
         const p=Object.getPrototypeOf(el);
         Object.getOwnPropertyDescriptor(p,'value').set.call(el,code[i]);
         el.dispatchEvent(new Event('input',{bubbles:true}));
         el.dispatchEvent(new Event('change',{bubbles:true}));});
       return 'digits:'+ins.length;
     }
     const i=ins[0]; i.focus();
     const p=Object.getPrototypeOf(i);
     Object.getOwnPropertyDescriptor(p,'value').set.call(i,code);
     i.dispatchEvent(new Event('input',{bubbles:true}));
     i.dispatchEvent(new Event('change',{bubbles:true}));
     return 'single:'+ins.length})()""".replace("((code)=>", "((%s)=>" % json.dumps(code)))
    print("code fill:", filled, "| code was", code)
    time.sleep(1)
    tab.click_text(["^continue$", "^log in$", "^submit$", "^next$"])
    time.sleep(10)
    url = tab.eval("location.href") or ""
    body = tab.eval(JS_BODY) or ""
    print("url:", url[:110])
    print(re.sub(r"\s+", " ", body)[:600])


def cmd_cases(tab):
    ensure_or_login(tab)
    time.sleep(3)
    cases = tab.eval(JS_CASES) or []
    for c in cases:
        print(c["id"], "|", c["row"][:160])
    if not cases:
        print("(no cases found — page text tail:")
        print(re.sub(r"\s+", " ", tab.eval(JS_BODY) or "")[-800:])
    return cases


def cmd_threads(tab):
    ensure_or_login(tab)
    time.sleep(2)
    th = tab.eval(JS_THREADS) or []
    if not th:
        surf = tab.eval(JS_OPEN_CASE_CHAT)
        if surf:
            print("opened case chat:", surf, file=sys.stderr)
            time.sleep(8)
            th = tab.eval(JS_THREADS) or []
    for t in th:
        print(t["id"], "|", t["txt"])
    if not th:
        print("(no threads — url:", tab.eval("location.href"), ")")
        print(re.sub(r"\s+", " ", tab.eval(JS_BODY) or "")[-800:])
    return th


def open_case_chat(tab, wait=8):
    """Open the support-case chat pane (specialist thread). Returns surface or None."""
    have = tab.eval("document.querySelectorAll('[data-surface*=\"maiba:thread_list_item:\"]').length")
    if have:
        return "threads-present"
    surf = tab.eval(JS_OPEN_CASE_CHAT)
    if surf:
        time.sleep(wait)
    return surf


def cmd_read(tab, substr=None, tail=6000):
    ensure_or_login(tab)
    tid = open_thread(tab, substr)
    if not tid:
        # specialist case chat has no thread_list_item — open case pane directly
        open_case_chat(tab)
        txt = tab.eval(JS_CHAT_TEXT) or ""
        if txt.strip():
            print("(case chat pane)")
            print(txt)
            return
        print("no thread matched", substr or "(any)")
        return
    print("THREAD", tid)
    print(tab.eval(JS_CHAT_TEXT) or "(empty)")


def cmd_send(tab, text, substr=None, wait_secs=0):
    ensure_or_login(tab)
    tid = open_thread(tab, substr)
    if not tid:
        open_case_chat(tab)
    print("THREAD", tid)
    before = tab.eval(JS_CHAT_TEXT) or ""
    box = tab.eval(JS_FIND_REPLY_BOX)
    if not box:
        print("no reply box — is the specialist chat open?")
        return
    print("box:", box)
    time.sleep(1)
    r = tab.eval(JS_TYPE_AND_SEND.replace("__TEXT__", json.dumps(text)))
    print("type:", r)
    time.sleep(1)
    s = tab.eval(JS_CLICK_SEND)
    print("send:", s)
    if wait_secs:
        deadline = time.time() + wait_secs
        last = ""
        while time.time() < deadline:
            time.sleep(10)
            cur = tab.eval(JS_CHAT_TEXT) or ""
            if cur != last:
                last = cur
                print("--- chat tail ---")
                print(cur[-2500:])
            if "Scroll to bottom" in cur or len(cur) > len(before) + 200:
                # keep polling a bit for stragglers
                pass
        print("--- final tail ---")
        print((tab.eval(JS_CHAT_TEXT) or "")[-3000:])
    else:
        time.sleep(6)
        print("--- tail ---")
        print((tab.eval(JS_CHAT_TEXT) or "")[-3000:])


def cmd_watch(tab, substr=None, interval=300):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    seen = {}
    if os.path.exists(STATE):
        seen = json.load(open(STATE)).get("seen", {})
    print(f"watching every {interval}s — Ctrl-C to stop")
    while True:
        try:
            ensure_or_login(tab)
            tid = open_thread(tab, substr, wait=5)
            if tid:
                txt = tab.eval(JS_CHAT_TEXT) or ""
                h = str(hash(txt[-3000:]))
                if seen.get(tid) != h:
                    if seen.get(tid) is not None:
                        alert = f"[meta-case] thread {tid} updated:\n{txt[-1500:]}"
                        print(alert)
                        try:
                            subprocess.run(
                                ["python3",
                                 "/home/tbaltzakis/cu130-slim/.devin/skills/"
                                 "telegram-alert-ops/scripts/tg_alert.py",
                                 "P2", alert[:3500]], timeout=20)
                        except Exception as e:
                            print("tg alert failed:", e)
                    seen[tid] = h
                    json.dump({"seen": seen}, open(STATE, "w"))
            else:
                print("no thread; cases:")
                cmd_cases(tab)
        except Exception as e:
            print("watch error:", e)
        time.sleep(interval)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    substr = None
    wait_secs = 0
    interval = 300
    texts = []
    i = 1
    while i < len(args):
        if args[i] == "--wait":
            wait_secs = int(args[i + 1]); i += 2
        elif args[i] == "--interval":
            interval = int(args[i + 1]); i += 2
        else:
            texts.append(args[i]); i += 1
    # for send: last non-flag arg after the message is substr — ambiguous;
    # convention: send "msg" [substr]; read [substr]
    base = _cdp_base()
    print("CDP:", base, file=sys.stderr)
    tab = Tab(base, match="business-support-home", create=True)
    try:
        if cmd == "login":
            print("login:", fb_password_login(tab))
        elif cmd == "login2fa":
            cmd_login2fa(tab)
        elif cmd == "cases":
            cmd_cases(tab)
        elif cmd == "threads":
            cmd_threads(tab)
        elif cmd == "read":
            cmd_read(tab, texts[0] if texts else None)
        elif cmd == "send":
            if not texts:
                sys.exit("send needs message text")
            msg = texts[0]
            sub = texts[1] if len(texts) > 1 else None
            cmd_send(tab, msg, sub, wait_secs)
        elif cmd == "watch":
            cmd_watch(tab, texts[0] if texts else None, interval)
        else:
            print(__doc__)
    finally:
        tab.close()


if __name__ == "__main__":
    main()
