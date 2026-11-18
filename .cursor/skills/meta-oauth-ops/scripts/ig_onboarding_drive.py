#!/usr/bin/env python3
"""Full-chain IG API Onboarding driver — mints the FB-Login-for-Business URL,
drives consent → wizard → IG profile pick → TOTP → business_extension clicks,
and stops when the FB Page gains instagram_business_account.

Designed for Meta's "Temporarily Blocked" rate bucket: one full attempt per
INTERVAL (default 2h — faster retries re-trigger the block).

Run:  nohup python3 ig_onboarding_drive.py >> logs/ig_onboarding_drive.log 2>&1 &
"""
import json, re, subprocess, sys, time, urllib.parse, urllib.request
import os
import subprocess

# WSL host IP — the Windows gateway moves subnets on vSwitch rebuilds
# (172.22.144.1 -> 192.168.96.1 observed 2026-11-18). Resolve live.
try:
    WSL_HOST_IP = os.environ.get("WSL_HOST_IP") or subprocess.run(
        ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
        capture_output=True, text=True, timeout=5).stdout.strip() \
        or "172.22.144.1"
except Exception:
    WSL_HOST_IP = "172.22.144.1"


CDP = "http://" + WSL_HOST_IP + ":9229"
INTERVAL = 7200            # seconds between full attempts
MAX_ATTEMPTS = 12          # ~24h budget
ENV_PATH = "/home/tbaltzakis/cu130-slim/.env"
PAGE_ID = "116436681562585"   # cloudless.gr FB Page
IG_USERNAME = "cloudless.gr"
STATE = {
    "t": "10024d25-2ea4-40ea-86c6-b1a89f515a2e",  # overwritten by mint
}


def _container():
    """Active API container — blue-green deploys may leave only -green."""
    for c in ("social-api", "social-api-green"):
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", c],
                           capture_output=True, text=True)
        if r.stdout.strip() == "true":
            return c
    return "social-api"


def _ig_host(url):
    """True only for real instagram.com hosts — a substring match would let
    evil.example/?next=instagram.com qualify for credential entry."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host == "instagram.com" or host.endswith(".instagram.com")


# ---------- helpers ----------

def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=15).read())


def tab_by_url(substr):
    return next((t for t in tabs() if substr in t.get("url", "")), None)


def tabs_by_id(tab_id):
    return next((t for t in tabs() if t.get("id") == tab_id), {})


def ws_for(tab):
    import websocket
    return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30,
                                       suppress_origin=True)


class Tab:
    """Eval + trusted-click helper bound to one CDP target (re-resolves WS)."""

    def __init__(self, tab_id):
        self.id = tab_id

    def _send(self, method, params=None, mid=[0]):
        mid[0] += 1
        ws = ws_for({"webSocketDebuggerUrl": self._ws_url()})
        try:
            ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
            end = time.time() + 25
            while time.time() < end:
                try:
                    r = json.loads(ws.recv())
                except Exception:
                    return None
                if r.get("id") == mid[0]:
                    return r
        finally:
            ws.close()
        return None

    def _ws_url(self):
        t = next(x for x in tabs() if x.get("id") == self.id)
        return t["webSocketDebuggerUrl"]

    def ev(self, expr):
        r = self._send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if r is None:
            raise RuntimeError("WS-DIED")
        return r.get("result", {}).get("result", {}).get("value")

    def click_box(self, box):
        self._send("Input.dispatchMouseEvent",
                   {"type": "mousePressed", "x": box["x"], "y": box["y"],
                    "button": "left", "clickCount": 1})
        self._send("Input.dispatchMouseEvent",
                   {"type": "mouseReleased", "x": box["x"], "y": box["y"],
                    "button": "left", "clickCount": 1})

    def click_button(self, pattern, trusted=False):
        """Click a visible button whose text matches `pattern` (regex str)."""
        box = self.ev(f"""(()=>{{
          const want=/{pattern}/i;
          const vis=e=>e.getClientRects().length>0;
          const b=[...document.querySelectorAll('[role="button"],button,a')]
            .filter(e=>want.test((e.innerText||'').trim())&&vis(e)).pop();
          if(!b)return null;
          const r=b.getBoundingClientRect();
          return {{x:r.x+r.width/2,y:r.y+r.height/2}};
        }})()""")
        if not box:
            return False
        if trusted:
            self.click_box(box)
        else:
            self.ev(f"""(()=>{{
              const want=/{pattern}/i;
              const vis=e=>e.getClientRects().length>0;
              const b=[...document.querySelectorAll('[role="button"],button,a')]
                .filter(e=>want.test((e.innerText||'').trim())&&vis(e)).pop();
              b.click();return true}})()""")
        return True

    def text(self, n=400):
        # return FULL innerText — callers search markers anywhere in it;
        # chrome header (~500 chars) precedes the real dialog content
        return self.ev("document.body.innerText||''") or ""

    def url(self):
        for t in tabs():
            if t.get("id") == self.id:
                return t.get("url", "")
        return ""


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def totp():
    import pyotp
    env = open(ENV_PATH).read()
    sec = re.search(r"^META_TOTP_SECRET\s*=\s*(\S+)", env, re.M)
    return pyotp.TOTP(sec.group(1)).now() if sec else None


def mint_url():
    """Mint a fresh onboarding authorize URL inside social-api."""
    code = r'''
import json, sys
sys.path.insert(0,'/app')
from urllib.parse import quote
from app.core.config import settings
from app.core.security import sign_oauth_state
state = sign_oauth_state({'t': '10024d25-2ea4-40ea-86c6-b1a89f515a2e', 'p': 'instagram-onboarding'})
extras = json.dumps({'setup': {'channel': 'IG_API_ONBOARDING'}})
scope = 'instagram_basic,instagram_content_publish,instagram_manage_messages,pages_show_list,pages_read_engagement'
url = ('https://www.facebook.com/dialog/oauth?client_id=' + settings.FACEBOOK_CLIENT_ID
       + '&redirect_uri=' + settings.FACEBOOK_REDIRECT_URI + '&response_type=code&scope=' + scope
       + '&state=' + state + '&display=page&extras=' + quote(extras))
print(url)
'''
    out = subprocess.run(["docker", "exec", _container(), "python", "-c", code],
                         capture_output=True, text=True, timeout=60)
    for line in out.stdout.splitlines():
        if line.startswith("https://www.facebook.com/dialog/oauth"):
            return line.strip()
    raise RuntimeError(f"mint failed: {out.stdout[-200:]} {out.stderr[-200:]}")


def open_tab(url):
    req = urllib.request.Request(CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
                                 method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def close_tab(tab_id):
    try:
        urllib.request.urlopen(f"{CDP}/json/close/{tab_id}", timeout=10)
    except Exception:
        pass


def page_ig_linked():
    """True only once OUR Page (cloudless.gr) has the cloudless.gr IG account
    linked — a link on any other managed Page must not count as success."""
    code = r'''
import sys, asyncio, json, urllib.request
sys.path.insert(0,'/app')
from app.db.session import async_session_maker
from sqlalchemy import text
from app.core.security import decrypt_token
PAGE_ID = "''' + PAGE_ID + '''"
IG_USERNAME = "''' + IG_USERNAME + '''"
FIELDS = "id,name,instagram_business_account{username},connected_instagram_account"
async def main():
    async with async_session_maker() as s:
        r = await s.execute(text(
            "select account_type, access_token_enc from social_accounts "
            "where platform='facebook' and status='active' "
            "and account_type in ('user','page')"))
        toks = {row.account_type: decrypt_token(row.access_token_enc)
                for row in r if row.access_token_enc}
    tok_user = toks.get("user")
    tok_page = toks.get("page")
    d = None
    if tok_user:
        d = json.loads(urllib.request.urlopen(
            'https://graph.facebook.com/v21.0/me/accounts?access_token='+tok_user
            +'&fields='+FIELDS, timeout=15).read())
        pages = [p for p in d.get('data', []) if p.get('id') == PAGE_ID]
    elif tok_page:
        d = json.loads(urllib.request.urlopen(
            f'https://graph.facebook.com/v21.0/{PAGE_ID}?access_token={tok_page}'
            +'&fields='+FIELDS, timeout=15).read())
        pages = [d] if d.get('id') == PAGE_ID else []
    else:
        return
    for p in pages:
        ig = p.get('instagram_business_account') or {}
        uname = (ig.get('username') or '').lower()
        if uname == IG_USERNAME.lower():
            print('LINKED', ig.get('id'))
asyncio.run(main())
'''
    try:
        out = subprocess.run(["docker", "exec", _container(), "python", "-c", code],
                             capture_output=True, text=True, timeout=60)
        return "LINKED" in out.stdout
    except Exception:
        return False


# ---------- chain steps ----------

def drive_ig_tab(tab):
    """Handle whatever the IG/OIDC popup shows: cookie banner, profile pick,
    WhatsApp-vs-authenticator chooser, TOTP, save-login."""
    if not _ig_host(tabs_by_id(tab["id"]).get("url", "")):
        log(f"ig: refusing credential entry on non-instagram host: "
            f"{tabs_by_id(tab['id']).get('url', '')[:80]}")
        return "BAD_HOST"
    t = Tab(tab["id"])
    txt = t.text(600)
    # cookie consent (iframe coords are unreliable — DOM click)
    if "use of cookies" in txt:
        log("ig: cookie banner")
        t.click_button("decline optional cookies")
        time.sleep(3)
        txt = t.text(600)
    # profile picker
    if "Log into Instagram" in txt or "cloudless.gr" in txt:
        if t.ev("""(()=>{const els=[...document.querySelectorAll('*')].filter(e=>e.childNodes.length&&[...e.childNodes].some(n=>n.nodeType===3&&/^cloudless\\.gr$/i.test(n.textContent.trim())));const s=els[els.length-1];if(!s)return false;let el=s,d=0;while(el&&d<10){if(el.getAttribute('role')==='button'||el.tagName==='A')break;el=el.parentElement;d++}el.click();return true})()"""):
            log("ig: picked cloudless.gr")
            time.sleep(6)
            txt = t.text(600)
    # WhatsApp-code screen → switch to the authenticator lane first
    if ("WhatsApp" in txt or "code we sent" in txt) and "Try another way" in txt \
            and "authentication app" not in txt and "6-digit code" not in txt:
        log("ig: WhatsApp challenge — switching to authenticator")
        t.click_button("try another way")
        time.sleep(4)
        txt = t.text(600)
    # 2FA chooser — the "Authentication app" row is a label/radio, not a
    # button; click the row then its input, then the chooser's Continue
    # (the last Continue in DOM order — .pop() picks it correctly).
    if "confirmation methods" in txt or "available confirmation" in txt \
            or ("WhatsApp" in txt and "uthenticat" in txt):
        log("ig: choosing authenticator")
        picked = t.ev("""(()=>{const els=[...document.querySelectorAll('label,[role=radio],[role=button],button,a,li')]
          .filter(e=>/authentication app/i.test(e.innerText||'')&&e.getClientRects().length>0);
          const s=els[els.length-1]; if(!s) return false; s.click();
          const inp=s.querySelector('input[type=radio],input[type=checkbox]');
          if(inp&&!inp.checked)inp.click(); return true})()""")
        if not picked:
            t.click_button("authenticat")
        time.sleep(2)
        t.click_button("continue")
        time.sleep(5)
        txt = t.text(600)
    # authenticator code field
    if "authentication app" in txt or "6-digit code" in txt:
        code = totp()
        if not code:
            log("ig: NO META_TOTP_SECRET — cannot pass 2FA")
            return "NO_TOTP"
        t.ev(f"""(()=>{{
          const inp=[...document.querySelectorAll('input')].find(i=>i.type==='text'&&i.getClientRects().length>0);
          if(!inp)return 'noinput';
          const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
          s.call(inp,'{code}');inp.dispatchEvent(new Event('input',{{bubbles:true}}));return 'set'}})()""")
        # trust device for future runs
        t.ev("""(()=>{const c=[...document.querySelectorAll('input[type=checkbox]')].find(i=>i.getClientRects().length>0);if(c&&!c.checked)c.click();return true})()""")
        # submit via the form's own input[type=submit] — the hidden chooser's
        # "Continue" button stays mounted and .pop() clicks the wrong one
        submitted = t.ev("""(()=>{
          const s=[...document.querySelectorAll('input[type=submit]')].find(i=>i.getClientRects().length>0);
          if(s){s.click();return 'submit-input';}
          const f=[...document.querySelectorAll('form')].find(f=>f.getClientRects().length>0);
          if(f){f.requestSubmit();return 'requestSubmit';}
          return null;})()""")
        if not submitted:
            t.click_button("continue|submit")
        log("ig: TOTP submitted")
        time.sleep(6)
        txt = t.text(600)
    if "Save your login info" in txt:
        t.click_button("not now")
        time.sleep(6)
        txt = t.text(600)
    return txt


def attempt(n):
    log(f"attempt {n}: minting url")
    # resume an in-progress chain if one is open — else mint fresh
    existing = (tab_by_url("facebook_business_extension")
                or tab_by_url("dialog/oauth")
                or next((x for x in tabs()
                         if _ig_host(x.get("url", ""))
                         and "/accounts/login" in urllib.parse.urlparse(x.get("url", "")).path), None))
    if existing:
        log(f"  resuming existing tab {existing['id'][:8]} {existing['url'][:60]}")
        t = Tab(existing["id"])
        # close stale IG auth tabs from dead chains — they'd hijack priority-1
        for x in tabs():
            if (x["id"] != t.id and _ig_host(x.get("url", "")) and re.search(
                    r"/(accounts/(login|two_step)|auth_platform|oauth)",
                    urllib.parse.urlparse(x.get("url", "")).path)):
                close_tab(x["id"])
    else:
        try:
            url = mint_url()
        except Exception as e:
            log("mint failed:", e)
            return
        tab = open_tab(url)
        t = Tab(tab["id"])
    time.sleep(8)

    popups_done = 0   # IG login popups completed in this attempt
    seen_popups = set()  # popup target ids already counted — a popup still
                       # loading must not burn the retry budget
    need_reload = False  # parent went stale after a popup closed — reload once
    for step in range(16):
        try:
            cur = t.url()
        except Exception:
            # main tab closed mid-chain — re-attach to the dialog tab if one
            # survived; otherwise retry soon (not a rate-limit backoff)
            alt = tab_by_url("dialog/oauth") or tab_by_url("facebook_business_extension")
            if alt:
                log(f"  main tab died — re-attaching to {alt['id'][:8]}")
                t = Tab(alt["id"])
                continue
            log("  chain tab gone — retry soon")
            return "RETRY"
        try:
            txt = t.text(500)
        except Exception:
            txt = ""
        log(f"  step{step} {cur[:70]} :: {txt[:70]!r}")

        # --- priority 1: an IG auth popup open in ANOTHER tab ---
        ig = next((x for x in tabs()
                   if x["id"] != t.id
                   and re.search(r"/(accounts/(login|challenge|two_step)|oauth|auth_platform)",
                                 urllib.parse.urlparse(x.get("url", "")).path)
                   and _ig_host(x.get("url", ""))),
                  None)
        if ig:
            try:
                r = drive_ig_tab(ig)
            except Exception:
                # popup self-closed mid-drive — that IS completion
                r = None
            if r == "NO_TOTP":
                return
            if ig["id"] not in seen_popups:
                seen_popups.add(ig["id"])
                popups_done += 1
            need_reload = True   # parent needs a reload to see next sub-step
            if popups_done >= 4:
                # the wizard has 3 sub-steps (login/page-link/perms), each able
                # to fire a forced-auth popup — allow those +1 retry. Beyond
                # that the handoff is blocked; every extra forced-auth login
                # just refreshes Meta's rate bucket — stop and cool down
                log("  4 login popups w/o extension handoff — blocked, cooling down")
                return
            time.sleep(5)
            continue
        # --- priority 2: business_extension handoff anywhere ---
        ext = tab_by_url("facebook_business_extension")
        if ext:
            if ext["id"] != t.id:
                t = Tab(ext["id"])
                try:
                    txt = t.text(500)
                except Exception:
                    txt = ""
            if "Temporarily Blocked" in txt:
                log("  BLOCKED at extension — cooling down")
                return
            clicked = t.click_button("continue|allow|next|done|got it|finish|confirm|authorize|agree|get started|not now|ok")
            if not clicked:
                log("  extension idle — nothing clickable")
            time.sleep(7)
            continue

        if "Temporarily Blocked" in txt:
            log("  BLOCKED — cooling down")
            return
        if cur.rstrip("/").endswith("home.php") or cur.rstrip("/") == "https://www.facebook.com":
            log("  dialog chain died (home) — aborting attempt")
            return
        if "dialog/oauth" in cur and "Home" in txt and "Account settings" in txt and not re.search(r"Continue|Get started|Log [Ii]n|Connect", txt):
            # Dialog rendered the FB home nav instead of the consent UI —
            # reload once before giving up on the chain.
            log("  dialog mis-rendered home nav — reloading")
            t.ev(f"location.href={json.dumps(cur)}; 'nav'")
            time.sleep(8)
            continue
        if cur.startswith("https://social.cloudless.gr"):
            log("  CALLBACK REACHED")
            time.sleep(5)
            if page_ig_linked():
                log("  ✅ IG business account linked — done")
                sys.exit(0)
            return
        if _ig_host(cur):
            try:
                r = drive_ig_tab({"id": t.id})
            except Exception:
                # main IG tab self-closed mid-drive — re-attach next step
                alt = tab_by_url("dialog/oauth") or tab_by_url("facebook_business_extension")
                if alt:
                    t = Tab(alt["id"])
                    need_reload = True
                    continue
                return "RETRY"
            if r == "NO_TOTP":
                return
            time.sleep(5)
            continue
        if "dialog/oauth" in cur and ("Continue as" in txt or "Connect to Instagram" in txt):
            if need_reload:
                # a completed IG popup leaves the parent stale — reload so the
                # wizard surfaces the next sub-step ("Continue as" consent)
                log("  reloading parent after popup completion")
                t.ev("location.reload(); 'nav'")
                need_reload = False
                time.sleep(8)
                continue
            if "Get started" in txt:
                t.click_button("get started")           # JS click opens the popup
                time.sleep(5)
                if not any(x["id"] != t.id and _ig_host(x.get("url", ""))
                           for x in tabs()):
                    t.click_button("get started", trusted=True)  # real-mouse fallback
            else:
                t.click_button("continue")
            time.sleep(8)
            continue
        time.sleep(6)
    log("  step budget exhausted")
    if not popups_done:
        return "RETRY"   # zero progress = driver miss, not a rate-limit


def main():
    n = 0
    retries = 0
    while n < MAX_ATTEMPTS:
        n += 1
        try:
            if page_ig_linked():
                log("already linked — done")
                return
            if attempt(n) == "RETRY" and retries < 8:
                log("  transient chain break — retrying in 90s")
                retries += 1
                time.sleep(90)
                n -= 1          # transient breaks don't burn an attempt
                continue
            retries = 0
        except Exception as e:
            log("attempt error:", e)
        if page_ig_linked():
            log("linked during cooldown — done")
            return
        time.sleep(INTERVAL)
    log("gave up after MAX_ATTEMPTS")


if __name__ == "__main__":
    main()
