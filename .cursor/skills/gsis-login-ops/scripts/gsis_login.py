"""Standalone GSIS/TaxisNet auto-login driver (gsis-login-ops).

Drives the chrome-debug-profile CDP lane (:9229) through the GSIS OAuth
chain: portal login page -> TaxisNet credentials -> consent -> optional
LoginAMKA step -> verify auth marker.

Usage:
  python gsis_login.py --portal idika            # ensure session (login if needed)
  python gsis_login.py --portal idika --check    # report auth state only

Add portals in PORTALS below (url host match + auth regex + login url).
"""
import json, os, sys, time, urllib.request, urllib.parse

CDP = "http://localhost:9229"
KEYS = "C:/Users/tbaltzakis/AppData/Roaming/devin/secrets"
CREDS_FILE = KEYS + "/taxisnet.json"
CHAT_ID = "7017840434"

PORTALS = {
    "idika": {
        "host": "idika.gr",
        "url": "https://www.idika.gr/epidomapaidiou/2026/LandingPage",
        "login_url": ("https://www.idika.gr/epidomapaidiou/Account/Login"
                      "?ReturnUrl=%2Fepidomapaidiou%2F2026%2FLandingPage"),
        "oauth_button": "Gsis.OAuth2",
        "auth_expr": """(()=>{const t=document.body.innerText||'';
          return /Κωδικός\\s*αίτησης|Αιτήσεις που αφορούν το πρόσωπο/.test(t)?'auth':'guest'})()""",
    },
}


def _targets():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())


def _new_tab(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
                               method="PUT"), timeout=10).read())


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
                return None if "exceptionDetails" in r else r.get("result", {}).get("value")
        return None

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _tg(text):
    try:
        bot = open(KEYS + "/tg-bot-token").read().strip()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode(),
            timeout=15)
    except Exception as e:
        print("tg err", e)


def _tab_for(host):
    for t in _targets():
        if t.get("type") == "page" and host in t.get("url", ""):
            return t
    return None


def ensure_login(portal, notify=None, timeout=120):
    """Ensure the named portal session is authenticated; performs the GSIS
    chain when needed. Returns True/False."""
    p = PORTALS[portal]
    creds = json.load(open(CREDS_FILE)) if os.path.exists(CREDS_FILE) else {}
    t = _tab_for(p["host"])
    if t is None:
        t = _new_tab(p["url"])
        time.sleep(6)
    tab = Tab(t["webSocketDebuggerUrl"])
    try:
        if tab.eval(p["auth_expr"]) == "auth":
            return True
        u = tab.eval("location.href") or ""
        if "/Account/Login" not in u:
            tab.cmd("Page.navigate", {"url": p["login_url"]})
            time.sleep(5)
        btn = p.get("oauth_button", "Gsis.OAuth2")
        tab.eval(f"var b=document.getElementById('{btn}'); if(b) b.click(); 'ok'")

        deadline = time.time() + 45
        while time.time() < deadline:
            u = tab.eval("location.href") or ""
            if "oauth2.gsis.gr" in u:
                break
            time.sleep(2)
        else:
            if notify:
                notify("never reached GSIS login page")
            return False

        if tab.eval("!!document.getElementById('j_username')"):
            fill = """(()=>{const u=document.getElementById('j_username'),p=document.getElementById('j_password');
              if(!u||!p)return 'noform';u.value=%s;p.value=%s;
              u.dispatchEvent(new Event('input',{bubbles:true}));
              p.dispatchEvent(new Event('input',{bubbles:true}));return 'filled'})()""" % (
                json.dumps(creds.get("username", "")), json.dumps(creds.get("password", "")))
            if tab.eval(fill) != "filled":
                if notify:
                    notify("GSIS form not found (layout changed?)")
                return False
            tab.eval("var b=document.getElementById('btn-login-submit')||document.querySelector('button[type=submit],input[type=submit]');if(b)b.click();'ok'")

        deadline = time.time() + timeout
        amka_done = False
        while time.time() < deadline:
            u = tab.eval("location.href") or ""
            if p["host"] in u:
                time.sleep(2)
                if tab.eval(p["auth_expr"]) == "auth":
                    return True
                if not amka_done and "LoginAMKA" in u and creds.get("amka"):
                    r = tab.eval("""(()=>{const a=document.getElementById('AMKA');if(!a)return 'noamkam';
                      a.value=%s;a.dispatchEvent(new Event('input',{bubbles:true}));
                      const b=document.querySelector('input[type=submit],button[type=submit]');
                      if(b)b.click();return 'sent'})()""" % json.dumps(creds["amka"]))
                    amka_done = True
                    if r != "sent":
                        if notify:
                            notify("AMKA field not found")
                        return False
            tab.eval("""(()=>{if(!location.host.includes('gsis.gr'))return 'ok';
              const b=document.getElementById('btn-submit')||document.querySelector('button[type=submit],input[type=submit]');
              if(b){b.click();return 'gsis'}return 'ok'})()""")
            tab.eval("""(()=>{const bs=[...document.querySelectorAll('button,input[type=submit],a.btn')];
              const b=bs.find(x=>/συνέχεια|continue|αποδοχή|approve|επιβεβαίωση|αποστολή/i.test(x.innerText||x.value||''));
              if(b&&!/είσοδος|επιστροφή/i.test(x.innerText||x.value||''))b.click();return 'ok'})()""")
            time.sleep(3)
        if notify:
            notify("timed out waiting for redirect back (2FA or consent?)")
        return False
    except Exception as e:
        if notify:
            notify(f"auto-login error: {e}")
        return False
    finally:
        tab.close()


def main():
    portal = "idika"
    check_only = "--check" in sys.argv
    if "--portal" in sys.argv:
        portal = sys.argv[sys.argv.index("--portal") + 1]
    if check_only:
        p = PORTALS[portal]
        t = _tab_for(p["host"])
        tab = Tab(t["webSocketDebuggerUrl"])
        try:
            state = tab.eval(p["auth_expr"])
        finally:
            tab.close()
        print(portal, "authed" if state == "auth" else f"guest ({state})")
        return
    ok = ensure_login(portal, notify=_tg)
    print(portal, "login:", "ok" if ok else "FAILED")
    if ok:
        _tg(f"gsis-login: {portal} session OK")


if __name__ == "__main__":
    main()
