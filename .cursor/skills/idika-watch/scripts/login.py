"""Auto re-login for IDIKA epidoma-paidiou via GSIS/TaxisNet OAuth.
Drives the chrome-debug-profile (:9229) over CDP. Returns True when the
LandingPage session is authenticated."""
import json, os, time, urllib.request, urllib.parse

CDP = "http://localhost:9229"
KEYS = "C:/Users/tbaltzakis/AppData/Roaming/devin/secrets"
CREDS = KEYS + "/taxisnet.json"
URL = "https://www.idika.gr/epidomapaidiou/2026/LandingPage"


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
        self._deheadless()

    def _deheadless(self):
        """Strip "Headless" from the UA — IDIKA HTTP-500s on HeadlessChrome
        UAs. Per-tab override; harmless when already headed."""
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

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _page_tab():
    for t in _targets():
        if t.get("type") == "page" and "idika.gr" in t.get("url", ""):
            return t
    return None


AUTH_EXPR = """(()=>{
  const t=document.body.innerText||'';
  return /Κωδικός\\s*αίτησης|Αιτήσεις που αφορούν το πρόσωπο/.test(t) ? 'auth' : 'guest';
})()"""

LOGIN_URL = ("https://www.idika.gr/epidomapaidiou/Account/Login"
             "?ReturnUrl=%2Fepidomapaidiou%2F2026%2FLandingPage")


def ensure_login(notify=None):
    """Ensure IDIKA session is authenticated; performs GSIS login if needed."""
    tab = None
    try:
        t = _page_tab()
        if t is None:
            t = _new_tab(URL)
            time.sleep(6)
        tab = Tab(t["webSocketDebuggerUrl"])

        # already authenticated? (URL alone is unreliable — logged-out
        # LandingPage stays on the same URL with an empty table)
        if tab.eval(AUTH_EXPR) == "auth":
            return True

        # get to the login page
        u = tab.eval("location.href") or ""
        has_btn = bool(tab.eval("!!document.getElementById('Gsis.OAuth2')"))
        if ("/Account/Login" not in u) or not has_btn:
            tab.cmd("Page.navigate", {"url": LOGIN_URL})
            time.sleep(5)
        tab.eval("var b=document.getElementById('Gsis.OAuth2'); if(b) b.click(); 'ok'")
        deadline = time.time() + 45
        while time.time() < deadline:
            u = tab.eval("location.href") or ""
            if "oauth2.gsis.gr" in u:
                break
            time.sleep(2)
        else:
            if notify:
                notify("IDIKA auto-login: never reached GSIS login page")
            return False

        # fill credentials only if GSIS actually asks for them
        # (a live GSIS session skips login.jsp straight to /oauth/authorize)
        creds = json.load(open(CREDS))
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
                    notify("IDIKA auto-login: GSIS form not found (layout changed?)")
                return False
            tab.eval("var b=document.getElementById('btn-login-submit')||document.querySelector('button[type=submit],input[type=submit]'); if(b) b.click(); 'ok'")

        # ride the redirect chain back to idika.gr; handle the AMKA step
        deadline = time.time() + 120
        amka_done = False
        while time.time() < deadline:
            u = tab.eval("location.href") or ""
            if "idika.gr" in u:
                time.sleep(2)
                if tab.eval(AUTH_EXPR) == "auth":
                    return True
                # LoginAMKA step: "TaxisNet certified you, enter your AMKA"
                if not amka_done and "LoginAMKA" in u:
                    amka = creds.get("amka", "")
                    r = tab.eval("""(()=>{
                      const a=document.getElementById('AMKA');
                      if(!a) return 'noamkam';
                      a.value=%s; a.dispatchEvent(new Event('input',{bubbles:true}));
                      const b=document.querySelector('input[type=submit],button[type=submit]');
                      if(b) b.click(); return 'sent';
                    })()""" % json.dumps(amka))
                    amka_done = True
                    if r != "sent":
                        if notify:
                            notify("IDIKA auto-login: AMKA field not found")
                        return False
            # GSIS consent page: click its submit (#btn-submit / "Αποστολή")
            tab.eval("""(()=>{if(!location.host.includes('gsis.gr'))return 'ok';
              const b=document.getElementById('btn-submit')||document.querySelector('button[type=submit],input[type=submit]');
              if(b){b.click();return 'gsis-submit'}return 'ok'})()""")
            # possible intermediate consent/approve pages: click any obvious continue button
            tab.eval("""(()=>{const bs=[...document.querySelectorAll('button,input[type=submit],a.btn')];
              const b=bs.find(x=>/συνέχεια|continue|αποδοχή|approve|επιβεβαίωση|αποστολή/i.test(x.innerText||x.value||''));
              if(b && !/είσοδος|επιστροφή/i.test(x.innerText||x.value||'')) b.click(); return 'ok'})()""")
            time.sleep(3)
        if notify:
            notify("IDIKA auto-login: timed out waiting for redirect back (2FA or consent?)")
        return False
    except Exception as e:
        if notify:
            notify(f"IDIKA auto-login error: {e}")
        return False
    finally:
        if tab:
            tab.close()
