"""Generic authenticated-portal watcher engine (gov-portal-watch).

Drives the chrome-debug-profile CDP lane (:9229): reloads a portal page,
extracts rows via JS, diffs against a state file, and alerts Telegram.
Optionally performs a GSIS/TaxisNet (+AMKA) auto re-login when the
portal's auth marker disappears.

Config = JSON file, see watches/idika.json for the shape:
  name, url, auth_regex, extract_js, login ("gsis"|"none"),
  creds (path to {username,password,amka}), state_file, chat_id

Library + CLI: `python engine.py <config.json> [--ping|--relogin]`"""
import json, os, re, sys, time, urllib.request, urllib.parse

CDP = "http://localhost:9229"
KEYS = "C:/Users/tbaltzakis/AppData/Roaming/devin/secrets"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\tbaltzakis\chrome-debug-profile"
CHROME_ARGS = []
DEFAULT_CHAT = "7017840434"

# ---------- CDP plumbing ----------

def _targets():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())


def _new_tab(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
                               method="PUT"), timeout=10).read())


def _browser_healthy():
    """Real roundtrip: /json/version alone passes on a wedged browser whose
    page renderers are all dead (2026-11-15 IDIKA false '2FA or consent'
    alert). Open a throwaway tab and eval — that exercises the whole path."""
    try:
        urllib.request.urlopen(CDP + "/json/version", timeout=5).read()
    except Exception:
        return False
    t = None
    try:
        t = _new_tab("about:blank")
        tab = Tab(t["webSocketDebuggerUrl"])
        try:
            return tab.eval("1+1") == 2
        finally:
            tab.close()
    except Exception:
        return False
    finally:
        if t:
            _close_target(t.get("id", ""))


def _kill_debug_chrome():
    """Kill every chrome.exe carrying the configured --user-data-dir.
    The profile is single-instance, so this only ever hits this lane."""
    import subprocess
    prof = PROFILE.replace("/", "\\").rstrip("\\")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{prof}*' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
          "-ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   timeout=30, capture_output=True)
    # Wait for full teardown: process exit + profile file-handle release.
    # 2s raced chrome teardown before and the relaunched instance hit a
    # locked profile -> 'cannot read and write to data directory' dialog.
    import subprocess as _sp
    probe = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{prof}*' }} | "
             "Measure-Object | Select-Object -ExpandProperty Count")
    for _ in range(40):
        r = _sp.run(["powershell", "-NoProfile", "-Command", probe],
                    timeout=15, capture_output=True, text=True)
        if (r.stdout or "").strip() == "0":
            break
        time.sleep(0.5)
    time.sleep(2)  # settle: handles release just after process exit


def ensure_browser():
    if _browser_healthy():
        return
    _kill_debug_chrome()
    import subprocess
    port = CDP.rsplit(":", 1)[-1].split("/")[0]
    subprocess.Popen([CHROME, f"--remote-debugging-port={port}",
                      f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--no-default-browser-check", "about:blank"] + CHROME_ARGS,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(1)
        if _browser_healthy():
            return
    raise RuntimeError("chrome debug port never came up")


class Tab:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=45, suppress_origin=True)
        self.mid = 0
        self._deheadless()

    def _deheadless(self):
        """Strip "Headless" from the UA — some portals (IDIKA) HTTP-500 on
        HeadlessChrome UAs. Per-tab override; harmless when already headed."""
        try:
            ua = self.eval("navigator.userAgent") or ""
            clean = ua.replace("HeadlessChrome", "Chrome")
            if clean != ua:
                self.cmd("Network.enable")
                self.cmd("Network.setUserAgentOverride", {"userAgent": clean})
        except Exception:
            pass

    def cmd(self, method, params=None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        return self.mid

    def eval(self, expr):
        rid = self.cmd("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True, "awaitPromise": True})
        deadline = time.time() + 60
        while time.time() < deadline:
            m = json.loads(self.ws.recv())
            if m.get("id") == rid:
                r = m.get("result", {})
                if "exceptionDetails" in r:
                    return None
                return r.get("result", {}).get("value")
        return None

    def reload_and_eval(self, expr):
        self.cmd("Page.enable")
        self.cmd("Page.reload", {"ignoreCache": True})
        self.ws.settimeout(45)
        try:
            while True:
                m = json.loads(self.ws.recv())
                if m.get("method") == "Page.loadEventFired":
                    break
        except Exception:
            pass
        time.sleep(2)
        return self.eval(expr)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def find_tab(url_match):
    """url_match: substring of the portal URL (e.g. 'idika.gr')."""
    for t in _targets():
        if t.get("type") == "page" and url_match in t.get("url", ""):
            return t
    return None


def _close_target(tid):
    """Kill a zombie/crashed tab target so the next find_tab opens fresh."""
    try:
        urllib.request.urlopen(CDP + "/json/close/" + tid, timeout=5).read()
    except Exception:
        pass


def surface_window(host=None):
    """Bring this lane's browser window on-screen (manual-login fallback).
    Uses browser-level CDP: Browser.getWindowForTarget + setWindowBounds.
    No-op when the lane has no visible window or CDP is unreachable."""
    try:
        import websocket
        ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=5).read())
        bws = ver.get("webSocketDebuggerUrl")
        if not bws:
            return
        tid = None
        for t in _targets():
            if t.get("type") == "page" and (not host or host in t.get("url", "")):
                tid = t.get("id")
                break
        if not tid:
            return
        ws = websocket.create_connection(bws, timeout=10, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": "Browser.getWindowForTarget",
                                "params": {"targetId": tid}}))
            wid = None
            deadline = time.time() + 10
            while time.time() < deadline:
                m = json.loads(ws.recv())
                if m.get("id") == 1:
                    wid = (m.get("result") or {}).get("windowId")
                    break
            if wid is None:
                return
            ws.send(json.dumps({"id": 2, "method": "Browser.setWindowBounds",
                                "params": {"windowId": wid, "bounds": {
                                    "windowState": "normal",
                                    "left": 80, "top": 80, "width": 1100, "height": 800}}}))
            deadline = time.time() + 5
            while time.time() < deadline:
                m = json.loads(ws.recv())
                if m.get("id") == 2:
                    break
        finally:
            ws.close()
    except Exception:
        pass


# ---------- Telegram ----------

def _ntfy(text):
    """Best-effort ntfy fan-out — mirrors ops alerts to the phone lane."""
    try:
        import subprocess
        d = os.path.dirname(os.path.abspath(__file__))
        for _ in range(6):
            p = os.path.join(d, "scripts", "ntfy_alert.py")
            if os.path.exists(p):
                subprocess.run([sys.executable, p, text, "ops"],
                               timeout=15, capture_output=True)
                return
            d = os.path.dirname(d)
    except Exception:
        pass


def tg(text, chat_id=DEFAULT_CHAT):
    _ntfy(text)
    bot = open(KEYS + "/tg-bot-token").read().strip()
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{bot}/sendMessage", data=data, timeout=15)
    except Exception as e:
        print("tg err", e)


# ---------- GSIS / TaxisNet auto-login ----------

AUTH_EXPR = """(()=>{
  const t=document.body.innerText||'';
  return /Κωδικός\\s*αίτησης|Αιτήσεις που αφορούν το πρόσωπο/.test(t) ? 'auth' : 'guest';
})()"""

LOGIN_URL = ("https://www.idika.gr/epidomapaidiou/Account/Login"
             "?ReturnUrl=%2Fepidomapaidiou%2F2026%2FLandingPage")


def gsis_login(tab, cfg, creds, portal_url, notify=None, timeout=120):
    """Drive GSIS OAuth (+ optional LoginAMKA step) on an open Tab.
    creds: dict {username,password,amka?}. Returns True when the portal's
    auth_expr reports 'auth'. cfg keys: host, login_url, oauth_button,
    auth_expr (all optional except host/login_url for non-IDIKA portals)."""
    host = cfg.get("host") or re.sub(r"^https?://", "", portal_url).split("/")[0]
    login_url = cfg.get("login_url", portal_url)
    oauth_button = cfg.get("oauth_button", "Gsis.OAuth2")
    auth_expr = cfg.get("auth_expr", AUTH_EXPR)
    try:
        if tab.eval(auth_expr) == "auth":
            return True
        u = tab.eval("location.href") or ""
        has_btn = bool(tab.eval("!!document.getElementById('%s')" % oauth_button))
        if ("/Account/Login" not in u and "login" not in u.lower()) or not has_btn:
            tab.cmd("Page.navigate", {"url": login_url})
            time.sleep(5)
        tab.eval("var b=document.getElementById('%s'); if(b) b.click(); 'ok'" % oauth_button)

        deadline = time.time() + 45
        while time.time() < deadline:
            u = tab.eval("location.href") or ""
            if "oauth2.gsis.gr" in u:
                break
            time.sleep(2)
        else:
            if notify:
                notify("auto-login: never reached GSIS login page")
            return False

        has_form = tab.eval("!!document.getElementById('j_username')")
        if has_form:
            fill = """(()=>{
              const u=document.getElementById('j_username'), p=document.getElementById('j_password');
              if(!u||!p) return 'noform';
              u.value=%s; p.value=%s;
              u.dispatchEvent(new Event('input',{bubbles:true}));
              p.dispatchEvent(new Event('input',{bubbles:true}));
              return 'filled';
            })()""" % (json.dumps(creds["username"]), json.dumps(creds["password"]))
            if tab.eval(fill) != "filled":
                if notify:
                    notify("auto-login: GSIS form not found (layout changed?)")
                return False
            tab.eval("var b=document.getElementById('btn-login-submit')||document.querySelector('button[type=submit],input[type=submit]'); if(b) b.click(); 'ok'")

        deadline = time.time() + timeout
        amka_done = False
        while time.time() < deadline:
            u = tab.eval("location.href") or ""
            if host in u:
                time.sleep(2)
                if tab.eval(auth_expr) == "auth":
                    return True
                if not amka_done and "LoginAMKA" in u and creds.get("amka"):
                    r = tab.eval("""(()=>{
                      const a=document.getElementById('AMKA');
                      if(!a) return 'noamkam';
                      a.value=%s; a.dispatchEvent(new Event('input',{bubbles:true}));
                      const b=document.querySelector('input[type=submit],button[type=submit]');
                      if(b) b.click(); return 'sent';
                    })()""" % json.dumps(creds["amka"]))
                    amka_done = True
                    if r != "sent":
                        if notify:
                            notify("auto-login: AMKA field not found")
                        return False
            tab.eval("""(()=>{if(!location.host.includes('gsis.gr'))return 'ok';
              const b=document.getElementById('btn-submit')||document.querySelector('button[type=submit],input[type=submit]');
              if(b){b.click();return 'gsis-submit'}return 'ok'})()""")
            tab.eval("""(()=>{const bs=[...document.querySelectorAll('button,input[type=submit],a.btn')];
              const b=bs.find(x=>/συνέχεια|continue|αποδοχή|approve|επιβεβαίωση|αποστολή/i.test(x.innerText||x.value||''));
              if(b && !/είσοδος|επιστροφή/i.test(x.innerText||x.value||'')) b.click(); return 'ok'})()""")
            time.sleep(3)
        if notify:
            notify("auto-login: timed out waiting for redirect back (2FA or consent?)")
        return False
    except Exception as e:
        if notify:
            notify(f"auto-login error: {e}")
        return False


# ---------- watcher ----------

def _cfg_path(argv_cfg):
    return argv_cfg


def load_config(path):
    cfg = json.load(open(path, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(path))
    cfg.setdefault("state_file", os.path.join(base, cfg["name"] + "-state.json"))
    if "creds" in cfg and not os.path.isabs(cfg["creds"]):
        cfg["creds"] = os.path.join(base, cfg["creds"])
    cfg.setdefault("chat_id", DEFAULT_CHAT)
    # Per-portal browser lane: a portal may run on its own Chrome instance
    # (own CDP port + profile + launch args) so its pages never appear on
    # the user's desktop. Defaults preserve the shared :9229 debug lane.
    global CDP, PROFILE, CHROME_ARGS
    if cfg.get("cdp_port"):
        CDP = f"http://localhost:{int(cfg['cdp_port'])}"
    if cfg.get("profile"):
        PROFILE = cfg["profile"]
    CHROME_ARGS = list(cfg.get("chrome_args") or [])
    return cfg


def run_check(cfg):
    try:
        ensure_browser()
    except Exception as e:
        print("browser err", e)
        st = _load_state(cfg)
        if not st.get("down_alert"):
            tg(f"{cfg['name']}: could not reach the debug browser.", cfg["chat_id"])
            st["down_alert"] = True
            _save_state(cfg, st)
        return

    host = re.sub(r"^https?://", "", cfg["url"]).split("/")[0]
    raw = None
    for attempt in range(2):
        t = find_tab(host)
        if t is None:
            t = _new_tab(cfg["url"])
            time.sleep(6)
        try:
            tab = Tab(t["webSocketDebuggerUrl"])
            try:
                raw = tab.reload_and_eval(cfg["extract_js"])
            finally:
                tab.close()
            break
        except Exception as e:
            print("check attempt", attempt, "err", e)
            _close_target(t.get("id", ""))
            time.sleep(3)
    if not raw:
        print("no eval result")
        return
    try:
        data = json.loads(raw)
        rows = data["rows"] if isinstance(data, dict) and "rows" in data else data
    except Exception:
        rows = [str(raw)]
    sig = json.dumps(rows, ensure_ascii=False)
    authed = bool(re.search(cfg["auth_regex"], sig))

    if not authed:
        if cfg.get("login") != "gsis":
            return
        creds = json.load(open(cfg["creds"])) if cfg.get("creds") else {}
        ft = find_tab(host)
        if ft is None:
            print("relogin: portal tab vanished")
            return
        try:
            tab = Tab(ft["webSocketDebuggerUrl"])
        except Exception as e:
            print("relogin tab err", e)
            return
        try:
            ok = gsis_login(tab, cfg, creds, cfg["url"], notify=lambda m: tg(f"{cfg['name']}: {m}", cfg["chat_id"]))
        finally:
            tab.close()
        if ok:
            t = find_tab(host)
            tab = Tab(t["webSocketDebuggerUrl"])
            try:
                raw = tab.reload_and_eval(cfg["extract_js"])
            finally:
                tab.close()
            if raw:
                data = json.loads(raw)
                rows = data["rows"] if isinstance(data, dict) and "rows" in data else data
                sig = json.dumps(rows, ensure_ascii=False)
                _save_state(cfg, {"sig": sig, "rows": rows, "ts": time.time()})
                tg(f"{cfg['name']}: auto re-login succeeded — session restored.", cfg["chat_id"])
                return
        st = _load_state(cfg)
        if not st.get("login_alert"):
            surface_window(host)
            tg(f"{cfg['name']}: session expired and auto re-login FAILED — the watch browser window was brought on-screen; please log in manually.", cfg["chat_id"])
            st["login_alert"] = True
            _save_state(cfg, st)
        return

    st = _load_state(cfg)
    if st.get("sig") == sig:
        st.pop("login_alert", None); st.pop("down_alert", None)
        _save_state(cfg, st)
        return
    changed = st.get("sig") is not None
    if changed:
        tg(f"{cfg['name']} — CHANGE DETECTED\n" + "\n".join(map(str, rows[:20])), cfg["chat_id"])
    _save_state(cfg, {"sig": sig, "rows": rows, "ts": time.time()})
    print("changed" if changed else "baseline")


def run_ping(cfg):
    try:
        ensure_browser()
    except Exception as e:
        print("ensure_browser:", e)
        return
    host = re.sub(r"^https?://", "", cfg["url"]).split("/")[0]
    for attempt in range(2):
        t = find_tab(host)
        if t is None:
            t = _new_tab(cfg["url"])
            time.sleep(6)
        try:
            tab = Tab(t["webSocketDebuggerUrl"])
            try:
                tab.cmd("Page.enable")
                tab.cmd("Page.reload", {"ignoreCache": False})
                tab.ws.settimeout(30)
                deadline = time.time() + 30
                while time.time() < deadline:
                    m = json.loads(tab.ws.recv())
                    if m.get("method") == "Page.loadEventFired":
                        break
            finally:
                tab.close()
            return
        except Exception as e:
            print("ping attempt", attempt, "err", e)
            _close_target(t.get("id", ""))
            time.sleep(3)


def run_relogin(cfg):
    ensure_browser()
    host = re.sub(r"^https?://", "", cfg["url"]).split("/")[0]
    t = find_tab(host) or _new_tab(cfg["url"])
    time.sleep(6)
    creds = json.load(open(cfg["creds"])) if cfg.get("creds") else {}
    try:
        tab = Tab(t["webSocketDebuggerUrl"])
    except Exception as e:
        print("relogin tab err", e)
        tg(f"{cfg['name']}: could not attach to portal tab for re-login.", cfg["chat_id"])
        return
    try:
        ok = gsis_login(tab, cfg, creds, cfg["url"], notify=lambda m: tg(f"{cfg['name']}: {m}", cfg["chat_id"]))
    finally:
        tab.close()
    if ok:
        tg(f"{cfg['name']}: morning session check OK — sessions refreshed.", cfg["chat_id"])
    else:
        surface_window(host)
        tg(f"{cfg['name']}: morning re-login FAILED — the watch browser window was brought on-screen; please log in manually.", cfg["chat_id"])
    print("relogin:", "ok" if ok else "FAILED")


def _load_state(cfg):
    p = cfg["state_file"]
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def _save_state(cfg, st):
    json.dump(st, open(cfg["state_file"], "w", encoding="utf-8"), ensure_ascii=False)


def main():
    mode = sys.argv[2] if len(sys.argv) > 2 else "check"
    cfg = load_config(sys.argv[1])
    {"check": run_check, "ping": run_ping, "relogin": run_relogin}[mode](cfg)


if __name__ == "__main__":
    main()
