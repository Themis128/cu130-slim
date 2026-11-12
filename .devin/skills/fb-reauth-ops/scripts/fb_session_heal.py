#!/usr/bin/env python3
"""fb_session_heal.py — keep the :9229 debug-Chrome facebook.com session
alive so the OAuth-consent and Meta-Business lanes (fb_reauth,
meta_billing_watch, meta_payment_helper) never block on a manual login.

Same shape as li_session_heal.py but for Facebook:

  status   probe logged-in state + stored-cookie freshness (read-only)
  capture  (re)store the live facebook.com cookie set -> state dir (0600)
  heal     if logged out: inject stored cookies (c_user+xs) -> verify;
           else credential login via FACEBOOK_USERNAME/FACEBOOK_PASSWORD
           -> click through save-device/checkpoint buttons -> on 2FA pull
           the SMS code off the phone (phone-2fa-ops fb_code.py) and
           submit -> verify. Alerts TG #ops on human-needed (deduped).

CDP machinery is reused from li_session_heal (dedicated /json/new tab +
background-reader websocket — never hijacks existing tabs).

Auth cookies: c_user + xs are the critical pair; the full set is stored.
State: ~/.local/state/fb-debug-session/  (outside the repo — never commit)
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                     / "linkedin-portal-gates-ops" / "scripts"))
from li_session_heal import (CDP, _cdp_base, _close_tab,  # noqa: E402
                             _env)

REPO = Path(__file__).resolve().parents[4]
STATE_DIR = Path.home() / ".local/state/fb-debug-session"
COOKIE_FILE = STATE_DIR / "cookies.json"
STATE_FILE = STATE_DIR / "heal-state.json"
TG_ALERT = REPO / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"
FB_CODE = REPO / ".devin/skills/phone-2fa-ops/scripts/fb_code.py"

PROBE_URL = "https://www.facebook.com/"
LOGIN_URL = "https://www.facebook.com/login/"

EVAL_STATE = r"""
(() => {
  const url = location.href;
  const hasLoginForm = !!document.querySelector(
    '#email, input[name="email"], input[name="pass"]');
  const loggedIn = !!document.querySelector(
    '[aria-label="Your profile"], [aria-label="Account Controls"], '
    + 'div[role="navigation"] a[href*="/me"], '
    + '#feed_columns, [data-pagelet*="Feed"]') && !hasLoginForm;
  const checkpoint = /checkpoint|two_factor|approvals|login\.php|login\//
    .test(url);
  const captcha = !!document.querySelector(
    'iframe[src*="captcha"], #captcha, [class*="captcha"]');
  // generic checkpoint-ish headings FB shows mid-flow
  const body = (document.body ? document.body.innerText : '').slice(0, 400);
  const needCode = /enter the code|confirmation code|approvals code|two.factor/i.test(body);
  return JSON.stringify({url, loggedIn, hasLoginForm, checkpoint,
    captcha, needCode, title: document.title});
})()
"""

# Fill + submit the classic login form (React-controlled inputs need the
# native setter + input events).
EVAL_LOGIN = r"""
(() => {
  const user = %s, pass = %s;
  const set = (el, v) => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const e = document.querySelector('#email, input[name="email"]');
  const p = document.querySelector('#pass, input[name="pass"]');
  if (!e || !p) return 'no-form';
  set(e, user); set(p, pass);
  const b = document.querySelector('#loginbutton, button[name="login"], '
    + 'button[type="submit"]');
  if (b) b.click(); else if (e.form) e.form.submit();
  return 'submitted';
})()
"""

# Post-login interstitials — save-login / "This was me" / continue.
# Never clicks deny/cancel/not-you.
EVAL_CLICKTHROUGH = r"""
(() => {
  if (!/checkpoint|login|approvals/.test(location.href)) return 'skip';
  const els = [...document.querySelectorAll(
    'button,[role=button],input[type=submit],a[role=link]')];
  const b = els.find(e => /this was me|save|continue|not now|got it|ok$/i
    .test((e.innerText||e.value||e.getAttribute('aria-label')||'').trim())
    && (e.innerText||e.value||'').trim().length < 60
    && !/cancel|deny|not you|log out|decline/i.test(e.innerText||''));
  if (b) { b.click(); return 'clicked:' + (b.innerText||b.value||'')
    .trim().slice(0,40); }
  return 'no-button';
})()
"""

EVAL_2FA = r"""
(() => {
  const code = %s;
  const inp = document.querySelector(
    'input[name="approvals_code"], input[name="code"], '
    + 'input[type="tel"], input[autocomplete="one-time-code"], '
    + 'input[name="verification_code"]');
  if (!inp) return 'no-code-field';
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(inp, code);
  inp.dispatchEvent(new Event('input', {bubbles:true}));
  inp.dispatchEvent(new Event('change', {bubbles:true}));
  const b = [...document.querySelectorAll(
    'button,[role=button],input[type=submit]')].find(e =>
    /continue|submit|confirm|next|log in/i.test(
      (e.innerText||e.value||'').trim()));
  if (b) b.click();
  return 'code-submitted';
})()
"""


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


def _alert(text):
    if TG_ALERT.exists():
        subprocess.run([sys.executable, str(TG_ALERT), "P2", text],
                       capture_output=True, timeout=30)


def _fb_sms_code():
    """Newest Facebook 2FA SMS code off the phone, or None."""
    if not FB_CODE.exists():
        return None
    try:
        r = subprocess.run([sys.executable, str(FB_CODE), "--latest"],
                           capture_output=True, text=True, timeout=60)
        for tok in (r.stdout or "").split():
            if tok.isdigit() and 4 <= len(tok) <= 8:
                return tok
    except Exception:
        pass
    return None


def _find_fb_tab(base):
    """Existing facebook.com page target — fresh /json/new tabs to FB take
    ~60-90s before CDP evals respond (heavy renderer boot on a RAM-tight
    host), while already-loaded FB tabs answer instantly. Reuse one."""
    try:
        tabs = json.loads(urllib.request.urlopen(
            base + "/json", timeout=8).read())
    except Exception:
        return None
    for t in tabs:
        if (t.get("type") == "page"
                and "facebook.com" in (t.get("url") or "")):
            return t
    return None


def _eval_quick(c, expr, timeout=20):
    """Runtime.evaluate with a short fuse — FB tabs can wedge evals for
    ~45s during renderer boot; probe loops must not stall on that."""
    r = c.cmd("Runtime.evaluate", {
        "expression": expr, "returnByValue": True,
        "awaitPromise": True}, timeout=timeout)
    return r.get("result", {}).get("value")


def _wait_eval_live(c, tries=18):
    """Poll until the tab answers evals (FB renderer boot can take a
    while); returns True once responsive."""
    for _ in range(tries):
        if _eval_quick(c, "1", timeout=12) is not None:
            return True
        time.sleep(5)
    return False


def _open_fb_tab(base, url):
    req = urllib.request.Request(
        base + "/json/new?" + urllib.parse.quote(url, safe=""),
        method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def with_fb_tab(base, fn, settle_url=None):
    """Attach to an existing facebook.com tab (navigating it to
    settle_url if given), else open a fresh one. A wedged attached tab
    (evals never respond — happens to FB tabs spawned during RAM
    pressure) is closed and replaced with a fresh one. Always closes
    only tabs it created or proved dead."""
    url = settle_url or PROBE_URL
    tab = _find_fb_tab(base)
    created = False
    if tab is not None:
        try:
            c = CDP(tab["webSocketDebuggerUrl"])
        except Exception as e:
            # /json can list a target that already died — "No such target
            # id" on handshake. Treat it like a wedged tab.
            print(f"stale FB tab target ({e}) — replacing", flush=True)
            _close_tab(base, tab.get("id", ""))
            tab = None
    if tab is not None:
        try:
            if settle_url and settle_url not in (tab.get("url") or ""):
                c.cmd("Page.enable")
                c.cmd("Page.navigate", {"url": settle_url})
            if _wait_eval_live(c, tries=10):
                time.sleep(3)
                c.tab_id = tab.get("id", "")
                return fn(c)
        finally:
            c.close()
        # attached tab never answered — it's a wedged leftover; drop it
        # and fall through to a fresh tab
        print("attached FB tab unresponsive — replacing", flush=True)
        _close_tab(base, tab.get("id", ""))
    tab = _open_fb_tab(base, url)
    created = True
    try:
        c = CDP(tab["webSocketDebuggerUrl"])
    except Exception as e:
        # fresh target raced away — one retry with a brand-new tab
        print(f"fresh FB tab connect failed ({e}) — retrying once",
              flush=True)
        _close_tab(base, tab.get("id", ""))
        tab = _open_fb_tab(base, url)
        c = CDP(tab["webSocketDebuggerUrl"])
    try:
        if not _wait_eval_live(c):
            return {"ok": False, "reason": "tab-unresponsive"}
        time.sleep(3)
        c.tab_id = tab.get("id", "")
        return fn(c)
    finally:
        c.close()
        if created:
            _close_tab(base, tab.get("id", ""))


def probe_state(c):
    raw = _eval_quick(c, EVAL_STATE)
    try:
        return json.loads(raw) if raw else {"loggedIn": False,
                                             "url": "eval-failed"}
    except Exception:
        return {"loggedIn": False, "url": f"eval-bad:{str(raw)[:80]}"}


def cmd_status(_args):
    base = _cdp_base()
    if not base:
        print("no-cdp: tried localhost:9229, 172.22.144.1:9229, :9222")
        return 2
    cookies = _load_cookies()
    pair = {c.get("name") for c in cookies} >= {"c_user", "xs"}
    print(f"cdp: {base}")
    print(f"stored cookies: {len(cookies)} (c_user+xs: {'yes' if pair else 'NO'})")

    def fn(c):
        st = probe_state(c)
        print(f"page: {st.get('url')}")
        print(f"loggedIn: {st.get('loggedIn')}  checkpoint: "
              f"{st.get('checkpoint')}  captcha: {st.get('captcha')}  "
              f"needCode: {st.get('needCode')}")
        return st
    with_fb_tab(base, fn)
    return 0


def cmd_capture(_args):
    base = _cdp_base()
    if not base:
        print("no-cdp")
        return 2

    def fn(c):
        st = probe_state(c)
        if not st.get("loggedIn"):
            print(f"not logged in ({st.get('url')}) — cannot capture")
            return {"ok": False}
        cookies = c.get_cookies(urls=["https://www.facebook.com",
                                      "https://facebook.com",
                                      "https://business.facebook.com"])
        if not cookies:
            cookies = [c for c in c.get_cookies()
                       if "facebook.com" in (c.get("domain") or "")]
        _save_cookies(cookies)
        names = {c["name"] for c in cookies}
        print(f"captured {len(cookies)} facebook cookies "
              f"(c_user={'c_user' in names}, xs={'xs' in names})")
        return {"ok": True, "count": len(cookies)}
    res = with_fb_tab(base, fn)
    return 0 if res.get("ok") else 3


def _heal_with_cookies(base):
    cookies = _load_cookies()
    if not any(c.get("name") == "xs" for c in cookies):
        return {"ok": False, "reason": "no-stored-cookies"}

    def fn(c):
        c.cmd("Network.enable")
        r = c.set_cookies(cookies)
        if "_error" in r:
            return {"ok": False, "reason": f"setCookies:{r['_error']}"}
        c.cmd("Page.enable")
        c.cmd("Page.reload", {"ignoreCache": True})
        time.sleep(8)
        st = probe_state(c)
        return {"ok": bool(st.get("loggedIn")), "state": st,
                "reason": None if st.get("loggedIn")
                else "cookies-rejected"}
    return with_fb_tab(base, fn, settle_url=PROBE_URL)


def _drive_interstitials(c, rounds=4):
    """Click save-device/'This was me'/continue on checkpoint pages."""
    for _ in range(rounds):
        r = _eval_quick(c, EVAL_CLICKTHROUGH, timeout=15)
        if not r or r in ("skip", "no-button"):
            return r
        time.sleep(4)


def _credential_login(base):
    user = _env("FACEBOOK_USERNAME") or _env("FACEBOOK_EMAIL")
    pw = _env("FACEBOOK_PASSWORD")
    if not (user and pw):
        return {"ok": False, "reason": "no-credentials"}

    def _submit_flow(c):
        """Navigate to the login page, wait for the form, submit creds.
        Returns (ok, probe-dict)."""
        c.cmd("Page.enable")
        c.cmd("Page.navigate", {"url": LOGIN_URL})
        print("navigated to login — waiting for form", flush=True)
        time.sleep(8)
        # wait for the real form (FB may show a redirect/interstitial)
        form_ready = False
        probe = {}
        for _ in range(10):
            if not _wait_eval_live(c, tries=3):
                continue
            raw = _eval_quick(c, "JSON.stringify({f:!!document.querySelector("
                         "'#email,input[name=\"email\"]'),"
                         "u:location.href.slice(0,90)})")
            try:
                probe = json.loads(raw) if raw else {}
            except Exception:
                probe = {}
            if probe.get("f"):
                form_ready = True
                break
            time.sleep(3)
        if not form_ready:
            return False, probe
        print("login form ready — submitting", flush=True)
        r = _eval_quick(c, EVAL_LOGIN % (json.dumps(user), json.dumps(pw)), timeout=25)
        if r != "submitted":
            return False, {"login_eval": r}
        return True, probe

    def _swap_tab(c):
        """Renderer dead beyond reload — close the tab and open a fresh
        one at the login page. Checkpoint state is server-side, so the
        flow resumes on the new tab."""
        _close_tab(base, getattr(c, "tab_id", "") or "")
        c.close()
        tab = _open_fb_tab(base, LOGIN_URL)
        nc = CDP(tab["webSocketDebuggerUrl"])
        nc.tab_id = tab.get("id", "")
        if not _wait_eval_live(nc, tries=12):
            nc.close()
            return None
        nc.cmd("Page.enable")
        return nc

    def fn(c):
        ok, probe = _submit_flow(c)
        if not ok:
            reason = (f"login-eval:{probe['login_eval']}"
                      if probe.get("login_eval") else "no-login-form")
            return {"ok": False, "reason": reason, "state": probe}
        # post-submit flow: wait, click through interstitials, handle 2FA
        eval_fails = 0
        reloads = 0
        swaps = 0
        for attempt in range(20):
            time.sleep(5)
            st = probe_state(c)
            print(f"post-login poll {attempt}: url={st.get('url','')[:70]} "
                  f"in={st.get('loggedIn')} ckpt={st.get('checkpoint')} "
                  f"code={st.get('needCode')}", flush=True)
            if st.get("url") == "eval-failed":
                eval_fails += 1
                if eval_fails >= 3:
                    reloads += 1
                    if reloads >= 2 and swaps < 2:
                        # reload can't revive a dead renderer — replace
                        # the tab and resubmit credentials on the fresh
                        # one (seen 2026-11-12: reload looped 11 polls
                        # then gave up post-login-incomplete).
                        print("tab unrecoverable — swapping for a fresh tab",
                              flush=True)
                        nc = _swap_tab(c)
                        if nc is None:
                            return {"ok": False,
                                    "reason": "tab-swap-unresponsive"}
                        c = nc
                        swaps += 1
                        reloads = 0
                        eval_fails = 0
                        ok, probe = _submit_flow(c)
                        if not ok:
                            reason = (f"login-eval:{probe['login_eval']}"
                                      if probe.get("login_eval")
                                      else "no-login-form")
                            return {"ok": False, "reason": reason,
                                    "state": probe}
                        continue
                    # FB checkpoint pages can wedge the renderer — reload
                    # resumes the server-side checkpoint state.
                    print("tab wedged — reloading", flush=True)
                    c.cmd("Page.enable")
                    c.cmd("Page.reload", {"ignoreCache": True},
                          timeout=15)
                    eval_fails = 0
                    time.sleep(10)
                continue
            eval_fails = 0
            reloads = 0
            if st.get("loggedIn"):
                cookies = c.get_cookies(urls=[
                    "https://www.facebook.com", "https://facebook.com"])
                if cookies:
                    _save_cookies(cookies)
                return {"ok": True, "captured": len(cookies or []),
                        "state": st}
            if st.get("captcha"):
                return {"ok": False, "reason": "captcha", "state": st}
            if st.get("needCode"):
                code = _fb_sms_code()
                if not code:
                    return {"ok": False, "reason": "2fa-no-sms-code",
                            "state": st}
                _eval_quick(c, EVAL_2FA % json.dumps(code), timeout=25)
                continue
            _drive_interstitials(c, rounds=2)
        st = probe_state(c)
        return {"ok": False,
                "reason": "post-login-incomplete",
                "state": st}
    return with_fb_tab(base, fn, settle_url=LOGIN_URL)


def cmd_heal(_args):
    base = _cdp_base()
    if not base:
        print("no-cdp")
        return 2

    def check(c):
        return probe_state(c)
    st = with_fb_tab(base, check)
    if st.get("loggedIn"):
        print("already logged in")
        _save_state({**_state(), "last_result": "ok"})
        return 0
    print(f"logged out ({st.get('url')}) — attempting cookie heal")
    res = _heal_with_cookies(base)
    if res.get("ok"):
        print("cookie heal succeeded")
        _save_state({**_state(), "last_heal": time.time(),
                     "last_result": "cookie-heal-ok"})
        return 0
    print(f"cookie heal failed: {res.get('reason')}")
    res2 = _credential_login(base)
    if res2.get("ok"):
        print(f"credential login succeeded "
              f"({res2.get('captured', 0)} cookies stored)")
        _save_state({**_state(), "last_heal": time.time(),
                     "last_result": "credential-login-ok"})
        return 0
    st = res2.get("state") or {}
    reason = (res2.get("reason")
              or "still-logged-out") + f"@{(st.get('url') or '')[:60]}"
    print(f"credential login failed: {reason} — human needed")
    prev = _state()
    _save_state({**prev, "last_heal": time.time(),
                 "last_result": f"human-needed:{reason}"})
    last_reason = prev.get("last_alert_reason", "")
    last_ts = prev.get("last_alert_ts", 0)
    if reason != last_reason or time.time() - last_ts > 6 * 3600:
        _alert("FB :9229 debug-browser session logged out — auto-heal "
               f"failed ({reason}). One manual facebook.com login in the "
               "debug Chrome restores OAuth consent + billing lanes; then "
               "run fb_session_heal.py capture.")
        _save_state({**_state(), "last_alert_reason": reason,
                     "last_alert_ts": time.time()})
    return 3


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=
                                 argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["status", "capture", "heal"])
    args = ap.parse_args()
    return {"status": cmd_status, "capture": cmd_capture,
            "heal": cmd_heal}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
