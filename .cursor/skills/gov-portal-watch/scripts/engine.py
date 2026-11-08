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
DEFAULT_CHAT = "7017840434"

# ---------- CDP plumbing ----------

def _targets():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())


def _new_tab(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
                               method="PUT"), timeout=10).read())


def ensure_browser():
    try:
        urllib.request.urlopen(CDP + "/json/version", timeout=5).read()
        return
    except Exception:
        pass
    import subprocess
    subprocess.Popen([CHROME, "--remote-debugging-port=9229",
                      f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--no-default-browser-check", "about:blank"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(CDP + "/json/version", timeout=5).read()
            return
        except Exception:
            continue
    raise RuntimeError("chrome debug port never came up")


class Tab:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=45, suppress_origin=True)
        self.mid = 0

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


# ---------- Telegram ----------

def tg(text, chat_id=DEFAULT_CHAT):
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
        if "/Account/Login" not in u and "login" not in u.lower():
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
    t = find_tab(host)
    if t is None:
        t = _new_tab(cfg["url"])
        time.sleep(6)
    tab = Tab(t["webSocketDebuggerUrl"])
    try:
        raw = tab.reload_and_eval(cfg["extract_js"])
    finally:
        tab.close()
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
        tab = Tab(find_tab(host)["webSocketDebuggerUrl"])
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
            tg(f"{cfg['name']}: session expired and auto re-login FAILED — please log in manually in the debug Chrome window.", cfg["chat_id"])
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
    t = find_tab(host)
    if t is None:
        t = _new_tab(cfg["url"])
        time.sleep(6)
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


def run_relogin(cfg):
    ensure_browser()
    host = re.sub(r"^https?://", "", cfg["url"]).split("/")[0]
    t = find_tab(host) or _new_tab(cfg["url"])
    time.sleep(6)
    creds = json.load(open(cfg["creds"])) if cfg.get("creds") else {}
    tab = Tab(t["webSocketDebuggerUrl"])
    try:
        ok = gsis_login(tab, cfg, creds, cfg["url"], notify=lambda m: tg(f"{cfg['name']}: {m}", cfg["chat_id"]))
    finally:
        tab.close()
    if ok:
        tg(f"{cfg['name']}: morning session check OK — sessions refreshed.", cfg["chat_id"])
    else:
        tg(f"{cfg['name']}: morning re-login FAILED.", cfg["chat_id"])
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
