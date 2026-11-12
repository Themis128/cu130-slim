#!/usr/bin/env python3
"""li_session_heal.py — keep the :9229 LinkedIn portal session alive.

The LinkedIn developer-portal lane (member_dp_probe, portal-ops) runs on
the user's real Chrome via the :9229 relay. When LinkedIn logs that
session out (cookie expiry, checkpoint, password change elsewhere), every
portal gate blocks on a manual re-login. This tool closes the loop:

  --status    probe logged-in state + stored-cookie freshness (read-only)
  --capture   (re)store the live linkedin.com cookie set from a logged-in
              tab -> ~/.local/state/li-portal-session/cookies.json (0600)
  --heal      if logged out: inject stored cookies via CDP, reload, verify
  --login     credential login via LINKEDIN_USERNAME/LINKEDIN_EMAIL +
              LINKEDIN_PASSWORD from .env — best-effort; bails to
              human-needed on captcha/checkpoint/2FA
  --daemon    poll every --interval seconds; heal on logout, alert on
              human-needed (TG #ops via tg_alert.py, deduped)

CDP: dedicated tab via /json/new + background-reader websocket — never
hijacks existing tabs. Endpoints: localhost:9229, 172.22.144.1:9229,
localhost:9222 (direct debug Chrome). Requires websocket-client.

Cookie jar lives OUTSIDE the repo (~/.local/state) — session material is
never committed. li_at/JSESSIONID are the critical pair; the full set is
stored so a heal restores the exact session, not a partial one.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CDP_CANDIDATES = ["http://localhost:9229", "http://172.22.144.1:9229",
                  "http://localhost:9222"]
PROBE_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"
STATE_DIR = Path.home() / ".local/state/li-portal-session"
COOKIE_FILE = STATE_DIR / "cookies.json"
STATE_FILE = STATE_DIR / "heal-state.json"
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"  # cu130-slim/.env
TG_ALERT = Path(__file__).resolve().parents[4] / \
    ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"

EVAL_STATE = r"""
(() => {
  const url = location.href;
  const loggedIn = !!document.querySelector(
    'img.global-nav__me-photo, button.global-nav__primary-link-me, '
    + '[data-control-name="nav.settings"], .global-nav__me');
  const authwall = /authwall|login|checkpoint|uas\/login/.test(url);
  const signinBtn = !!([...document.querySelectorAll('a,button')]
    .find(e => /^sign in$|^log in$/i.test((e.innerText||'').trim())));
  const captcha = !!document.querySelector('iframe[src*="captcha"], '
    + '#captcha, [class*="captcha"], [id*="captcha"]');
  return JSON.stringify({url, loggedIn, authwall, signinBtn, captcha,
    title: document.title});
})()
"""

EVAL_LOGIN = r"""
(async () => {
  const user = %s, pass = %s;
  const u = document.querySelector('#username, input[name="session_key"]');
  const p = document.querySelector('#password, input[name="session_password"]');
  if (!u || !p) return JSON.stringify({ok:false, reason:'no-form', url:location.href});
  const set = (el, v) => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  set(u, user); set(p, pass);
  const btn = document.querySelector('button[type="submit"], '
    + '#session_key-login, form button');
  if (btn) btn.click(); else u.form && u.form.submit();
  return JSON.stringify({ok:true});
})()
"""


def _env(name):
    v = os.environ.get(name)
    if v:
        return v
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _cdp_base():
    for base in CDP_CANDIDATES:
        try:
            tabs = json.loads(urllib.request.urlopen(
                base + "/json", timeout=5).read())
            if tabs:
                return base
        except Exception:
            continue
    return None


def _open_tab(base, url):
    req = urllib.request.Request(
        base + "/json/new?" + urllib.parse.quote(url, safe=""),
        method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _close_tab(base, tab_id):
    try:
        urllib.request.urlopen(base + "/json/close/" + tab_id, timeout=5)
    except Exception:
        pass


class CDP:
    def __init__(self, ws_url):
        import threading
        import websocket
        self.ws_url = ws_url
        self.ws = None
        self._responses = {}
        self._cv = threading.Condition()
        self._dead = None
        self._mid = 0
        self._connect()
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _connect(self):
        import websocket
        self.ws = websocket.create_connection(self.ws_url, timeout=30,
                                              suppress_origin=True)
        self._dead = None

    def _reconnect(self):
        try:
            self._connect()
            return True
        except Exception:
            return False

    def _reader(self):
        try:
            while True:
                msg = json.loads(self.ws.recv())
                mid = msg.get("id")
                if mid is not None:
                    with self._cv:
                        self._responses[mid] = msg.get("result", {})
                        self._cv.notify_all()
        except Exception as e:
            self._dead = e
            with self._cv:
                self._cv.notify_all()

    def cmd(self, method, params=None, timeout=45):
        for attempt in (0, 1):
            r = self._cmd_once(method, params, timeout)
            if "_error" not in r:
                return r
            if attempt == 0 and self._reconnect():
                continue
            return r
        return {"_error": "unreachable"}

    def _cmd_once(self, method, params, timeout):
        self._mid += 1
        mid = self._mid
        try:
            self.ws.send(json.dumps({"id": mid, "method": method,
                                     "params": params or {}}))
        except Exception as e:
            return {"_error": str(e)}
        deadline = time.time() + timeout
        with self._cv:
            while mid not in self._responses:
                if self._dead:
                    return {"_error": f"ws closed: {self._dead}"}
                remaining = deadline - time.time()
                if remaining <= 0:
                    return {"_error": "timeout"}
                self._cv.wait(remaining)
            return self._responses.pop(mid)

    def eval(self, expr):
        r = self.cmd("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": True})
        return r.get("result", {}).get("value")

    def get_cookies(self, urls=None):
        params = {}
        if urls:
            params["urls"] = urls
        r = self.cmd("Storage.getCookies", params, timeout=30)
        return r.get("cookies", [])

    def set_cookies(self, cookies):
        # Network.setCookies accepts the same cookie-object shape that
        # Storage.getCookies returns (name/value/domain/path/expires/...).
        return self.cmd("Network.setCookies", {"cookies": cookies},
                        timeout=30)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(s):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=1))


def _load_cookies():
    try:
        return json.loads(COOKIE_FILE.read_text())
    except Exception:
        return []


def _save_cookies(cookies):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps(cookies, indent=1))
    os.chmod(COOKIE_FILE, 0o600)


DOCKER_CANDIDATES = [
    "/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/"
    "resources/bin/docker.exe",
    "docker",
]
DB_QUERY = ("SELECT meta_data->'browser_storage_state' FROM social_accounts "
            "WHERE platform='linkedin' "
            "AND meta_data->'browser_storage_state' IS NOT NULL LIMIT 1")


def _seed_from_db():
    """Pull the sidecar-synced browser_storage_state for linkedin accounts
    out of Postgres and store it as the CDP cookie jar. Returns count."""
    import shutil
    adb = None
    for d in DOCKER_CANDIDATES:
        if d.endswith(".exe") or shutil.which(d):
            adb = d
            break
    if not adb:
        return 0
    r = subprocess.run(
        [adb, "exec", "social-postgres", "psql", "-U", "social_user",
         "-d", "social_automation", "-t", "-A", "-c", DB_QUERY],
        capture_output=True, text=True, timeout=30)
    raw = (r.stdout or "").strip()
    if r.returncode != 0 or not raw:
        return 0
    try:
        state = json.loads(raw.splitlines()[0])
    except Exception:
        return 0
    cookies = state.get("cookies") or []
    cookies = [c for c in cookies
               if "linkedin.com" in (c.get("domain") or "")]
    if not any(c.get("name") == "li_at" for c in cookies):
        return 0
    _save_cookies(cookies)
    return len(cookies)


def _alert(text):
    if TG_ALERT.exists():
        subprocess.run([sys.executable, str(TG_ALERT), "P2", text],
                       capture_output=True, timeout=30)


def probe_state(c):
    raw = c.eval(EVAL_STATE)
    try:
        return json.loads(raw) if raw else {"loggedIn": False,
                                             "url": "eval-failed"}
    except Exception:
        return {"loggedIn": False, "url": f"eval-bad:{str(raw)[:80]}"}


def with_tab(base, url, fn, settle=9):
    """Open a dedicated tab, run fn(cdp), close it. Returns fn's result."""
    tab = _open_tab(base, url)
    time.sleep(settle)
    c = CDP(tab["webSocketDebuggerUrl"])
    try:
        return fn(c)
    finally:
        c.close()
        _close_tab(base, tab.get("id", ""))


def cmd_status(args):
    base = _cdp_base()
    if not base:
        print("no-cdp: tried " + ", ".join(CDP_CANDIDATES))
        return 2
    cookies = _load_cookies()
    li_at = [c for c in cookies if c.get("name") == "li_at"]
    age = ""
    if COOKIE_FILE.exists():
        age = f", jar mtime {time.strftime('%Y-%m-%d %H:%M', time.localtime(COOKIE_FILE.stat().st_mtime))}"
    print(f"cdp: {base}")
    print(f"stored cookies: {len(cookies)} "
          f"(li_at: {'yes' if li_at else 'NO'}){age}")

    def fn(c):
        st = probe_state(c)
        print(f"page: {st.get('url')}")
        print(f"loggedIn: {st.get('loggedIn')}  authwall: {st.get('authwall')}"
              f"  captcha: {st.get('captcha')}")
        return st
    with_tab(base, PROBE_URL, fn)
    return 0


def cmd_capture(args):
    base = _cdp_base()
    if not base:
        print("no-cdp")
        return 2

    def fn(c):
        st = probe_state(c)
        if not st.get("loggedIn"):
            print(f"not logged in ({st.get('url')}) — cannot capture")
            return {"ok": False, "state": st}
        cookies = c.get_cookies(urls=["https://www.linkedin.com",
                                      "https://linkedin.com"])
        if not cookies:
            cookies = c.get_cookies()
            cookies = [c for c in cookies
                       if "linkedin.com" in (c.get("domain") or "")]
        _save_cookies(cookies)
        names = {c["name"] for c in cookies}
        print(f"captured {len(cookies)} linkedin cookies "
              f"(li_at={'li_at' in names}, "
              f"JSESSIONID={'JSESSIONID' in names})")
        return {"ok": True, "count": len(cookies)}
    res = with_tab(base, PROBE_URL, fn)
    return 0 if res.get("ok") else 3


def _heal_with_cookies(base):
    cookies = _load_cookies()
    if not cookies or not any(c.get("name") == "li_at" for c in cookies):
        # Jar empty/stale — try seeding from the sidecar-synced DB state
        # (session_healer keeps browser_storage_state fresh on linkedin
        # accounts whenever the :9225 sidecar is logged in).
        n = _seed_from_db()
        if n:
            print(f"seeded {n} cookies from social_accounts "
                  "browser_storage_state")
        cookies = _load_cookies()
    if not cookies:
        return {"ok": False, "reason": "no-stored-cookies"}
    needed = {"li_at", "JSESSIONID"}
    have = {c.get("name") for c in cookies}
    if not needed <= have:
        return {"ok": False,
                "reason": f"jar-missing:{sorted(needed - have)}"}

    def fn(c):
        c.cmd("Network.enable")
        r = c.set_cookies(cookies)
        if "_error" in r:
            return {"ok": False, "reason": f"setCookies:{r['_error']}"}
        # reload the probe page so the new cookies take effect
        c.cmd("Page.enable")
        c.cmd("Page.reload", {"ignoreCache": True})
        time.sleep(8)
        st = probe_state(c)
        return {"ok": bool(st.get("loggedIn")), "state": st,
                "reason": None if st.get("loggedIn")
                else "cookies-rejected"}
    tab = _open_tab(base, PROBE_URL)
    time.sleep(6)
    c = CDP(tab["webSocketDebuggerUrl"])
    try:
        return fn(c)
    finally:
        c.close()
        _close_tab(base, tab.get("id", ""))


def _credential_login(base):
    user = _env("LINKEDIN_USERNAME") or _env("LINKEDIN_EMAIL")
    pw = _env("LINKEDIN_PASSWORD")
    if not (user and pw):
        return {"ok": False, "reason": "no-credentials"}

    def fn(c):
        c.cmd("Page.enable")
        c.cmd("Page.navigate", {"url": LOGIN_URL})
        # Cloudflare "Just a moment" interstitial precedes the real form —
        # it auto-clears in a real browser profile, usually <30s. Poll for
        # the username field instead of a fixed sleep.
        form_ready = False
        for _ in range(15):
            time.sleep(4)
            r = c.eval("JSON.stringify({u:!!document.querySelector("
                       "'#username, input[name=\"session_key\"]'),"
                       "t:document.title,url:location.href.slice(0,90)})")
            try:
                probe = json.loads(r) if r else {}
            except Exception:
                probe = {}
            if probe.get("u"):
                form_ready = True
                break
        if not form_ready:
            return {"ok": False, "reason": "cloudflare-challenge",
                    "state": probe}
        st = probe_state(c)
        if st.get("captcha"):
            return {"ok": False, "reason": "captcha", "state": st}
        expr = EVAL_LOGIN % (json.dumps(user), json.dumps(pw))
        c.eval(expr)
        time.sleep(10)
        st = probe_state(c)
        if st.get("captcha"):
            return {"ok": False, "reason": "captcha", "state": st}
        if st.get("authwall") and not st.get("loggedIn"):
            return {"ok": False, "reason": "checkpoint-or-authwall",
                    "state": st}
        ok = bool(st.get("loggedIn"))
        out = {"ok": ok, "state": st}
        if ok:
            cookies = c.get_cookies(urls=["https://www.linkedin.com",
                                          "https://linkedin.com"])
            if cookies:
                _save_cookies(cookies)
                out["captured"] = len(cookies)
        return out
    tab = _open_tab(base, LOGIN_URL)
    time.sleep(6)
    c = CDP(tab["webSocketDebuggerUrl"])
    try:
        return fn(c)
    finally:
        c.close()
        _close_tab(base, tab.get("id", ""))


def cmd_heal(args):
    base = _cdp_base()
    if not base:
        print("no-cdp")
        return 2

    def check(c):
        return probe_state(c)
    st = with_tab(base, PROBE_URL, check, settle=8)
    if st.get("loggedIn"):
        print("already logged in")
        return 0
    print(f"logged out ({st.get('url')}) — attempting cookie heal")
    res = _heal_with_cookies(base)
    if res.get("ok"):
        print("cookie heal succeeded")
        _save_state({**_state(), "last_heal": time.time(),
                     "last_result": "cookie-heal-ok"})
        return 0
    print(f"cookie heal failed: {res.get('reason') or (res.get('state') or {}).get('url', '?')}")
    res2 = _credential_login(base)
    if res2.get("ok"):
        print(f"credential login succeeded ({res2.get('captured', 0)} cookies stored)")
        _save_state({**_state(), "last_heal": time.time(),
                     "last_result": "credential-login-ok"})
        return 0
    st = res2.get("state") or {}
    reason = (res2.get("reason")
              or ("still-logged-out" if not res2.get("ok") else "ok")
              + f"@{(st.get('url') or '')[:60]}")
    print(f"credential login failed: {reason} — human login needed")
    prev = _state()
    _save_state({**prev, "last_heal": time.time(),
                 "last_result": f"human-needed:{reason}"})
    # Dedup alerts: only re-alert when the reason changes or 6h passed.
    last_reason = prev.get("last_alert_reason", "")
    last_ts = prev.get("last_alert_ts", 0)
    if reason != last_reason or time.time() - last_ts > 6 * 3600:
        _alert("LinkedIn :9229 portal session logged out — auto-heal failed "
               f"({reason}). One manual login on the debug browser restores it; "
               "then run li_session_heal.py --capture.")
        _save_state({**_state(), "last_alert_reason": reason,
                     "last_alert_ts": time.time()})
    return 3


def cmd_daemon(args):
    interval = args.interval
    alerted = False
    while True:
        rc = cmd_heal(args)
        if rc == 3 and not alerted:
            alerted = True
        elif rc == 0:
            if alerted:
                _alert("LinkedIn :9229 portal session healed — automation restored.")
            alerted = False
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["status", "capture", "heal", "daemon", "seed"])
    ap.add_argument("--interval", type=int, default=900)
    args = ap.parse_args()
    if args.cmd == "seed":
        n = _seed_from_db()
        print(f"seeded {n} cookies" if n else "seed failed — docker/DB unreachable or no linkedin state")
        return 0 if n else 2
    return {"status": cmd_status, "capture": cmd_capture,
            "heal": cmd_heal, "daemon": cmd_daemon}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
