#!/usr/bin/env python3
"""X account recovery daemon — runs on Windows against probe Chrome CDP :9444.

Phase A: password-reset loop (survives X 'too many attempts' rate limit)
Phase B: OAuth authorize click -> social.cloudless.gr callback stores tokens

Logs to x_daemon_log.txt; writes x_daemon_status.json on each transition.
"""
import json, sys, time, re, base64, urllib.request, urllib.parse, os, traceback
import websocket

CDP_PORT = os.environ.get("X_CDP_PORT", "9444")
CDP = f"http://127.0.0.1:{CDP_PORT}"
DIR = r"C:\Users\tbaltzakis"
SUF = "" if CDP_PORT == "9444" else f"_{CDP_PORT}"
LOG = os.path.join(DIR, f"x_daemon_log{SUF}.txt")
STATUS = os.path.join(DIR, f"x_daemon_status{SUF}.json")
GMAIL_SECRET = os.path.join(DIR, "AppData", "Roaming", "devin", "secrets")
RESET_ID = os.environ.get("X_RESET_ID", "baltzakis.themis@gmail.com")
PASSWORD = open(os.path.join(DIR, "x_new_password.txt")).read().strip()
AUTH_URL = open(os.path.join(DIR, "x_oauth_url.txt")).read().strip()
if os.environ.get("X_OAUTH_URL_FILE"):
    AUTH_URL = open(os.environ["X_OAUTH_URL_FILE"]).read().strip()
MAX_SECONDS = int(os.environ.get("X_MAX_SECONDS", str(12 * 3600)))
RETRY_SLEEP = int(os.environ.get("X_RETRY_SLEEP", "1500"))  # 25 min — account-level rate limit needs a quiet window; hammering keeps it alive

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"

def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass

def status(phase, detail=""):
    try:
        json.dump({"phase": phase, "detail": detail, "ts": time.time()},
                  open(STATUS, "w"))
    except Exception:
        pass

def alert(text):
    """Telegram alert via x_tg_alert.json ({token, chat}) — silent if absent."""
    try:
        cfg = json.load(open(os.path.join(DIR, "x_tg_alert.json")))
        url = f"https://api.telegram.org/bot{cfg['token']}/sendMessage"
        urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps({"chat_id": cfg["chat"],
                                  "text": "X-recovery: " + text}).encode(),
            headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass

def page_ws():
    tabs = json.loads(urllib.request.urlopen(CDP + "/json", timeout=8).read())
    pages = [t for t in tabs if t.get("type") == "page"]
    xpages = [t for t in pages if "x.com" in t.get("url", "") or "twitter.com" in t.get("url", "")]
    t = (xpages or pages)[0]
    return websocket.create_connection(t["webSocketDebuggerUrl"], timeout=25, suppress_origin=True)

class PG:
    def __init__(self):
        self.ws = page_ws()
        self._id = 0
        self.send("Network.enable")
        self.send("Network.setUserAgentOverride", {"userAgent": UA, "acceptLanguage": "en-US,en;q=0.9"})
        self.send("Page.enable")
    def send(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._id:
                return m
    def ev(self, expr):
        try:
            r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
            res = r.get("result", {}).get("result", {})
            return res.get("value", res.get("description", ""))
        except Exception as e:
            return f"__ERR__{e}"
    def nav(self, url):
        self.send("Page.navigate", {"url": url})
    def trusted_click_re(self, pattern):
        """Real mouse click at the center of the first element whose label
        matches the regex — X's onboarding SPA ignores synthetic .click()."""
        rect = self.ev("""(()=>{const els=[...document.querySelectorAll('button,[role=button],a,[role=link],div[role=button]')];
          const re=new RegExp(%s,'i');
          const b=els.find(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&re.test((e.innerText||'').trim())});
          if(!b)return null;const r=b.getBoundingClientRect();
          return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2,t:(b.innerText||'').trim().slice(0,40)})})()""" % json.dumps(pattern))
        if not rect or rect == "null":
            return "none"
        try:
            rc = json.loads(rect)
        except Exception:
            return "badrect"
        x, y = rc["x"], rc["y"]
        self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        time.sleep(0.3)
        self.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        time.sleep(0.1)
        self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        return "trusted:" + rc.get("t", "")
    def txt(self, n=600):
        return str(self.ev("(document.body&&document.body.innerText||'').slice(0,%d)" % n) or "")
    def url(self):
        return str(self.ev("location.href") or "")
    def close(self):
        try: self.ws.close()
        except Exception: pass

def fill(pg, sel, val):
    return pg.ev("""(()=>{const i=document.querySelector(%s);if(!i)return 'no-input';
      const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
      s.call(i,%s);i.dispatchEvent(new Event('input',{bubbles:true}));i.dispatchEvent(new Event('change',{bubbles:true}));
      i.dispatchEvent(new KeyboardEvent('keydown',{key:'a',bubbles:true}));return 'ok'})()""" % (json.dumps(sel), json.dumps(val)))

def click_re(pg, pattern):
    # trusted mouse click first (SPA needs real input), JS click as fallback
    r = pg.trusted_click_re(pattern)
    if r != "none":
        return r
    return pg.ev("""(()=>{const els=[...document.querySelectorAll('button,[role=button],a,[role=link],div[role=button]')];
      const re=new RegExp(%s,'i');
      const b=els.find(e=>re.test((e.innerText||'').trim()));
      if(b){b.click();return 'js:'+((b.innerText||'').trim().slice(0,40))}return 'none'})()""" % json.dumps(pattern))

# ---- gmail ----
def gmail_token():
    tok = json.load(open(os.path.join(GMAIL_SECRET, "gmail-token.json")))
    keys = json.load(open(os.path.join(GMAIL_SECRET, "gcp-oauth.keys.json")))
    inst = keys.get("installed") or keys.get("web")
    data = urllib.parse.urlencode({"client_id": inst["client_id"], "client_secret": inst["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=15))["access_token"]

def latest_x_code(since_ms):
    try:
        at = gmail_token()
        q = urllib.parse.urlencode({"q": "from:x.com newer_than:2d", "maxResults": 8})
        msgs = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + q,
            headers={"Authorization": "Bearer " + at}), timeout=15).read()).get("messages", [])
        best = None
        for m in msgs:
            try:
                u = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}?format=full"
                d = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": "Bearer " + at}), timeout=15).read())
                ts = int(d["internalDate"])
                if ts < since_ms:
                    continue
                hdrs = {h["name"]: h["value"] for h in d["payload"]["headers"]}
                subj = hdrs.get("Subject", "")
                sl = subj.lower()
                if not ("verification code" in sl or "password reset" in sl or "reset" in sl):
                    continue
                body = ""
                def walk(p):
                    nonlocal body
                    if p.get("mimeType", "").startswith("text/") and p.get("body", {}).get("data"):
                        body += base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", "replace")
                    for part in p.get("parts", []): walk(part)
                walk(d["payload"])
                # X puts the confirmation code early in the body text; restrict
                # to the first chunk so footer/URL digits don't win
                head = re.sub(r"<[^>]+>", " ", body)[:1500]
                codes = re.findall(r"\b(\d{6})\b", head)
                if codes and (best is None or ts > best[0]):
                    best = (ts, codes[0], subj)
            except Exception:
                continue
        return best
    except Exception as e:
        log("gmail err:", e)
        return None

# ---- phases ----
def detect(pg):
    """Return one of: rate_limited, code_step, pw_step, method_step, user_step, logged_in_wall, unknown"""
    t = pg.txt(700).lower()
    u = pg.url()
    if "too many attempts" in t:
        return "rate_limited"
    if "enter the code" in t or "verification code" in t or "code we sent" in t:
        return "code_step"
    if ("new password" in t or "create a password" in t or "choose a password" in t) and "password" in t:
        return "pw_step"
    if "find your account" in t or "email or username" in t:
        return "user_step"
    if "@" in t and ("email" in t or "send" in t or "recover" in t):
        return "method_step"
    return "unknown"

def reset_attempt(pg):
    """One pass through the reset state machine. Returns 'done'|'retry'|'wait_code'."""
    state = detect(pg)
    log("state:", state, "| url:", pg.url()[:80])
    if state == "rate_limited":
        return "retry"
    if state == "user_step":
        log("fill:", fill(pg, "#jf-input-username_or_email", RESET_ID))
        time.sleep(1)
        log("click:", click_re(pg, "^continue$"))
        time.sleep(9)
        return "again"
    if state == "method_step":
        # the recovery-method button IS the submit — trusted click on the
        # masked-email option (only element containing '@' on this screen)
        log("pick:", click_re(pg, "@"))
        time.sleep(9)
        return "again"
    if state == "code_step":
        return "wait_code"
    if state == "pw_step":
        pws = pg.ev("JSON.stringify([...document.querySelectorAll('input[type=password]')].map(i=>i.id||i.name||'pw'))")
        try:
            ids = json.loads(pws) if isinstance(pws, str) and pws.startswith("[") else []
        except Exception:
            ids = []
        sels = ["#" + i if i else "input[type=password]" for i in (ids[:2] or ["", ""])]
        for s in sels:
            log("fill pw:", fill(pg, s, PASSWORD))
            time.sleep(1)
        log("click pw:", click_re(pg, "^continue$|^next$|^save$|^reset$|^done$"))
        time.sleep(10)
        return "again"
    return "unknown_state"

def logged_in(pg):
    pg.nav("https://x.com/home")
    time.sleep(9)
    t = pg.txt(500).lower()
    u = pg.url()
    wall = any(w in u for w in ("login", "signin", "i/flow", "onboarding")) or \
           any(w in t for w in ("sign in", "log in", "create account", "happening now"))
    log("login check url:", u[:80], "| wall:", wall)
    return not wall

def oauth_phase(pg):
    status("oauth", "navigating to authorize")
    pg.nav(AUTH_URL)
    time.sleep(10)
    log("oauth page:", pg.url()[:100], "|", pg.txt(300)[:200])
    # trust checkbox then authorize — retry a few times
    for i in range(4):
        cb = pg.ev("""(()=>{const c=document.querySelector('[role=checkbox],input[type=checkbox]');if(c&&!c.checked){c.click();return 'checked'}return c?'already':'none'})()""")
        log("checkbox:", cb)
        time.sleep(2)
        ac = click_re(pg, "authorize|approve|allow")
        log("authorize click:", ac)
        time.sleep(8)
        u = pg.url()
        if "cloudless.gr" in u or "callback" in u or "oauth" in u and "x.com" not in u:
            break
    # wait for the redirect to land on the callback
    for i in range(12):
        u = pg.url()
        log("wait redirect", i, u[:110])
        if "oauth/twitter/callback" in u or "social.cloudless.gr" in u:
            status("done", "callback reached: " + u[:120])
            log("CALLBACK REACHED:", u[:150])
            return True
        time.sleep(5)
    status("oauth_stuck", pg.url()[:120])
    return False

def main():
    open(LOG, "a").close()
    t0 = time.time()
    status("start", "daemon boot")
    log("=== x_daemon start ===")
    flow_start_ms = int(time.time() * 1000) - 3600000  # accept codes from last hour
    unknown_streak = 0
    while time.time() - t0 < MAX_SECONDS:
        try:
            pg = PG()
        except Exception as e:
            log("cdp connect fail:", e); time.sleep(60); continue
        try:
            # ensure we're on the reset page
            if "password_reset" not in pg.url() and "jf/" not in pg.url():
                pg.nav("https://x.com/account/begin_password_reset")
                time.sleep(10)
            # drive reset state machine for up to ~3 min per cycle
            for _ in range(20):
                r = reset_attempt(pg)
                if r == "retry":
                    status("rate_limited", "waiting")
                    log("rate limited — sleeping %ds" % RETRY_SLEEP)
                    time.sleep(RETRY_SLEEP)
                    pg.nav("https://x.com/account/begin_password_reset")
                    time.sleep(10)
                    break
                if r == "wait_code":
                    # poll gmail up to 5 min for the code, then enter
                    got = None
                    for i in range(30):
                        time.sleep(10)
                        got = latest_x_code(flow_start_ms)
                        if got:
                            break
                    if not got:
                        log("no code after 5min — restarting flow")
                        break
                    log("code:", got[1], got[2])
                    ins = str(pg.ev("JSON.stringify([...document.querySelectorAll('input')].map(i=>i.id||i.name||i.type))") or "")
                    sel = "#jf-input-verification_code" if "verification_code" in ins else "input[type=text],input[inputmode=numeric],input[type=tel]"
                    log("fill code:", fill(pg, sel, got[1]))
                    time.sleep(1)
                    log("click code:", click_re(pg, "^continue$|^verify$|^next$|^submit$"))
                    time.sleep(9)
                    continue
                if r == "unknown_state":
                    t = pg.txt(300)
                    log("unknown state text:", t[:200])
                    # maybe logged-in landing — check
                    if logged_in(pg):
                        break
                    unknown_streak += 1
                    if unknown_streak == 3:
                        alert(f"stuck on unrecognized X screen x3 — likely a human factor (Arkose/phone/checkpoint). url={pg.url()[:100]} text={t[:150]}")
                    time.sleep(15)
                    continue
                unknown_streak = 0
                time.sleep(3)
            else:
                pass
            # after each cycle, test login
            if logged_in(pg):
                status("logged_in", "reset complete — starting oauth")
                log("LOGGED IN — oauth phase")
                if oauth_phase(pg):
                    status("done", "oauth callback reached")
                    alert("DONE — OAuth callback reached, tokens stored")
                    log("=== DONE ===")
                    return
                alert("oauth did not complete — page: " + pg.url()[:120])
                # if oauth didn't complete, loop back (session persists)
                time.sleep(30)
                continue
            time.sleep(20)
        except Exception:
            log("cycle error:\n" + traceback.format_exc()[-800:])
            time.sleep(60)
        finally:
            pg.close()
    status("timeout", "gave up after %ds" % MAX_SECONDS)
    alert(f"daemon timed out after {MAX_SECONDS//3600}h — supervisor will relaunch")

if __name__ == "__main__":
    main()
