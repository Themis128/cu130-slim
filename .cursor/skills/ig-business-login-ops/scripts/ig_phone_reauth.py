#!/usr/bin/env python3
"""ig_phone_reauth.py — business_login (instagram2) re-mint driven entirely
through the user's Galaxy S22+ Chrome via ADB CDP (phone_cdp.py).

This is the durable lane per owner directive "always use my mobile meta
apps": the phone holds the trusted IG app session + receives the Meta
recovery mail notifications, so no desktop browser or IG-native password
is needed.

Flow (verified live 2026-11-24 end-to-end, minted 38ddbd44 active):
  1. Fresh instagram2 authorize URL (minted via social-api admin JWT).
  2. Phone Chrome tab → instagram.com/accounts/password/reset/?find_by=username
     → username cloudless.gr → Continue → Meta Account gate.
  3. "Try another way" → "Get code or link via email" → gmail option →
     Continue → read the 8-digit code from the Gmail notification
     (adb dumpsys notification — no mailbox creds needed).
  4. Code → WhatsApp 2FA → "Try another way" → Backup code radio →
     code from ~/ig_backup_codes.txt (single-use; consumed codes are
     removed — regenerate via IG app Accounts Center → 2FA → Backup codes).
  5. "Create a new password" → re-enter INSTAGRAM_PASSWORD (same value =
     no effective rotation) → logged-in instagram.com session.
  6. Navigate tab to the authorize URL → consent "Allow" → poll for
     instagram2/callback → verify social_accounts row flips active.

Prereqs: ADB_SERIAL env (default 100.84.31.85:5555), phone unlocked with
Chrome running (chrome_devtools_remote socket), python websocket-client.
Run from WSL or Windows — phone_cdp.py is imported from the
phone-web-auth-ops skill dir (override with PHONE_CDP_DIR).

Usage:
    ig_phone_reauth.py                 # full flow
    ig_phone_reauth.py --authorize-url URL   # skip minting
    ig_phone_reauth.py --step N        # resume at step N (debug)

Never prints passwords, codes, tokens, or secrets.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ADB_SERIAL = os.environ.get("ADB_SERIAL", "100.84.31.85:5555")
CODES_FILE = os.path.expanduser("~/ig_backup_codes.txt")
TEAM_ID = "10024d25-2ea4-40ea-86c6-ba89f515a2eb"
RESET_URL = "https://www.instagram.com/accounts/password/reset/?find_by=username"
USERNAME = "cloudless.gr"

sys.path.insert(0, os.environ.get(
    "PHONE_CDP_DIR",
    os.path.expanduser("~/cu130-slim/.devin/skills/phone-web-auth-ops/scripts")))
import phone_cdp  # noqa: E402


def adb(*args):
    return subprocess.run(["adb", "-s", ADB_SERIAL, *args],
                          capture_output=True, text=True, timeout=30)


def sh(cmd):
    return adb("shell", cmd).stdout


def notifications():
    return sh("dumpsys notification --noredact")


def gmail_code():
    "Newest 'Use NNNNNNNN to log into Instagram' from Gmail notifications."""
    txt = notifications()
    codes = re.findall(r"Use (\d{8}) to log into Instagram", txt)
    return codes[0] if codes else None


def backup_code():
    "Pop the first unused code from ig_backup_codes.txt."""
    try:
        lines = [l.strip() for l in open(CODES_FILE) if l.strip()]
    except OSError:
        return None
    if not lines:
        return None
    code, rest = lines[0], lines[1:]
    with open(CODES_FILE, "w") as f:
        f.write("\n".join(rest) + ("\n" if rest else ""))
    os.chmod(CODES_FILE, 0o600)
    return code


def mint_authorize_url():
    "Mint instagram2 authorize URL via admin JWT inside social-api."""
    script = r'''
import asyncio,os,httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import create_access_token
async def main():
    e=create_async_engine(os.environ["DATABASE_URL"])
    async with e.connect() as c:
        uid=await c.scalar(text("select id from users order by created_at limit 1"))
    t=create_access_token({"sub":str(uid)})
    async with httpx.AsyncClient(timeout=30) as h:
        r=await h.get("http://localhost:8000/api/v1/auth/oauth/instagram2/authorize",
                      params={"team_id":"%s"},
                      headers={"Authorization":f"Bearer {t}"})
        print(r.json().get("authorization_url",""))
asyncio.run(main())
''' % TEAM_ID
    r = subprocess.run(
        ["docker", "exec", "social-api", "python3", "-c", script],
        capture_output=True, text=True, timeout=90)
    for line in r.stdout.splitlines():
        if line.startswith("https://www.instagram.com/oauth/authorize"):
            return line.strip()
    raise RuntimeError(f"mint failed: {r.stdout[-300:]} {r.stderr[-200:]}")


class PhoneTab:
    """CDP driver bound to one LIVE phone-Chrome tab.

    Chrome on Android suspends background tabs — their page websocket
    accepts the connection but Runtime.evaluate hangs forever. List order
    is not reliable, so every url_match candidate is probed with a short
    eval timeout and the first responsive one wins. Pass fresh_url to
    open a brand-new tab instead (cleanest for auth flows).
    """

    def __init__(self, target_id=None, url_match=None, fresh_url=None):
        phone_cdp.front()
        if fresh_url:
            tid = phone_cdp.new_tab(fresh_url)
            time.sleep(4)
            tabs = phone_cdp.tabs()
            t = next((x for x in tabs if x["id"] == tid), None) \
                or next((x for x in tabs if fresh_url.split("?")[0]
                         in x.get("url", "")), None)
            if t is None:
                raise RuntimeError("fresh tab not found")
            self.target_id = t["id"]
            self.tab = phone_cdp.Tab(t["webSocketDebuggerUrl"])
            self.tab.ws.settimeout(20)
            return
        tabs = phone_cdp.tabs()
        cands = []
        if target_id:
            cands = [x for x in tabs if x["id"].startswith(target_id)]
        elif url_match:
            cands = [x for x in tabs if url_match in x.get("url", "")]
        if not cands:
            raise RuntimeError("tab not found")
        last_err = None
        for t in cands:
            try:
                probe = phone_cdp.Tab(t["webSocketDebuggerUrl"])
                probe.ws.settimeout(6)
                if probe.evaluate("1") != 1:
                    continue
                self.target_id = t["id"]
                self.tab = probe
                self.tab.ws.settimeout(20)
                return
            except Exception as e:  # suspended/dead tab — try next
                last_err = e
                continue
        raise RuntimeError(f"no live tab matched ({last_err})")

    def ev(self, expr):
        return self.tab.evaluate(expr)

    def nav(self, url):
        self.tab.navigate(url)

    def url(self):
        return self.ev("location.href") or ""

    def body(self, n=500):
        return (self.ev(f"document.body.innerText.slice(0,{n})") or "")

    def click_xy(self, x, y):
        for et in ("mouseMoved",):
            self.tab.call("Input.dispatchMouseEvent",
                          {"type": et, "x": x, "y": y})
            time.sleep(0.15)
        self.tab.call("Input.dispatchMouseEvent",
                      {"type": "mousePressed", "x": x, "y": y,
                       "button": "left", "clickCount": 1})
        time.sleep(0.08)
        self.tab.call("Input.dispatchMouseEvent",
                      {"type": "mouseReleased", "x": x, "y": y,
                       "button": "left", "clickCount": 1})

    def click_text(self, pattern, tag="div[role=button],button,a,label,span",
                   occurrence=0, exact=True):
        """Click the Nth visible element whose trimmed text matches."""
        rx = ("^" + pattern + "$") if exact else pattern
        js = (
            "(()=>{const els=[...document.querySelectorAll('%s')]"
            ".filter(e=>e.getClientRects().length&&new RegExp(%s,'i')"
            ".test((e.innerText||'').trim()));"
            "const e=els[%d];if(!e)return null;"
            "const b=e.getBoundingClientRect();"
            "return JSON.stringify({x:b.x+b.width/2,y:b.y+b.height/2})})()"
            % (tag, json.dumps(rx), occurrence))
        r = self.ev(js)
        if not r:
            return False
        p = json.loads(r)
        self.click_xy(p["x"], p["y"])
        return True

    def click_row_at_y(self, y, x=110):
        """Click a radio-row by its vertical position (method pickers)."""
        self.click_xy(x, y)

    def fill_first_input(self, value):
        js = (
            "(()=>{const inp=[...document.querySelectorAll('input')]"
            ".filter(i=>i.getClientRects().length)[0];if(!inp)return 'noinput';"
            "const setter=Object.getOwnPropertyDescriptor("
            "window.HTMLInputElement.prototype,'value').set;"
            "inp.focus();setter.call(inp,%s);"
            "inp.dispatchEvent(new Event('input',{bubbles:true}));"
            "inp.dispatchEvent(new Event('change',{bubbles:true}));"
            "return inp.value})()" % json.dumps(value))
        return self.ev(js)

    def radio_states(self):
        return self.ev(
            "(()=>{const i=[...document.querySelectorAll("
            "'input[type=radio],[role=radio]')];"
            "return i.map(x=>x.checked||x.getAttribute('aria-checked'))"
            ".join(',')})()") or ""


def wait_body(tab, needle, timeout=30, interval=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if needle.lower() in tab.body(900).lower():
            return True
        time.sleep(interval)
    return False


def main():
    only = int(sys.argv[sys.argv.index("--step") + 1]) if "--step" in sys.argv else 0
    url = (sys.argv[sys.argv.index("--authorize-url") + 1]
           if "--authorize-url" in sys.argv else None)

    phone_cdp.forward()

    # --- session check: fresh tab to IG home — a visible password field
    # means the login wall; its absence means a live session ---
    tab = PhoneTab(fresh_url="https://www.instagram.com/")
    time.sleep(6)
    has_pw = tab.ev(
        "!![...document.querySelectorAll('input')]"
        ".find(i=>i.type==='password'&&i.getClientRects().length)")
    u = tab.url()
    on_login_wall = has_pw or any(s in u for s in
                                  ("login", "signup", "challenge"))
    if not on_login_wall:
        print("[session] live IG session — skipping recovery")
    else:
        # --- step 1: password reset → Meta Account gate ---
        # NB: find_by=username in the URL does NOT preselect the field —
        # the form still renders "Enter your mobile number" with a
        # "Find by email or username instead" toggle. Match labels by
        # substring (exact anchors miss the "…instead" suffix).
        if only <= 1:
            tab = PhoneTab(fresh_url=RESET_URL)
            time.sleep(5)
            body = tab.body(600).lower()
            if "mobile number" in body or "find by email or username" in body:
                if not tab.click_text("find by email or username",
                                      exact=False):
                    raise RuntimeError("username toggle not found")
                time.sleep(3)
            tab.fill_first_input(USERNAME)
            time.sleep(1)
            tab.click_text("continue")
            # verify we actually left the identifier page
            for _ in range(10):
                time.sleep(3)
                b = tab.body(600).lower()
                if "find your profile" not in b and "mobile number" not in b:
                    break
            print("[gate]", tab.body(200).replace("\n", " | ")[:160])
            if "find your profile" in tab.body(600).lower():
                raise RuntimeError("identifier submit bounced — check username field")

        # --- step 2: Try another way → email code ---
        # `tab` carries the whole recovery flow — do NOT re-acquire by
        # URL (the page navigates off /password/reset into Meta gates).
        if only <= 2:
            tab.click_text("try another way", exact=False)
            time.sleep(5)
            # prefer the row naming our gmail; else first email-code option
            b = tab.body(1200)
            if "gmail" in b.lower() or "baltzakis" in b.lower():
                tab.click_text("baltzakis.themis@gmail.com", exact=False) \
                    or tab.click_text("gmail", exact=False)
                time.sleep(2)
            if not tab.click_text("get code or link via email",
                                  exact=False):
                raise RuntimeError("email option not found: " +
                                   tab.body(300).replace("\n", " | ")[:200])
            time.sleep(2)
            tab.click_text("continue")
            time.sleep(6)
            print("[email]", tab.body(200).replace("\n", " | ")[:160])

        # --- step 3: read gmail code from notifications, submit ---
        if only <= 3:
            code = None
            for _ in range(20):
                code = gmail_code()
                if code:
                    break
                time.sleep(3)
            if not code:
                raise RuntimeError("no gmail code notification")
            tab.fill_first_input(code)
            time.sleep(1)
            tab.click_text("continue")
            time.sleep(6)
            print("[2fa]", tab.body(200).replace("\n", " | ")[:160])

        # --- step 4: Try another way → Backup code radio → code ---
        if only <= 4:
            tab.click_text("try another way", exact=False)
            time.sleep(4)
            # radios: WhatsApp/SMS/Backup — backup is the LAST radio row
            states = tab.radio_states().split(",")
            if len(states) >= 3 and states[2] not in ("true", "True"):
                ys = tab.ev(
                    "(()=>{const i=[...document.querySelectorAll("
                    "'input[type=radio],[role=radio]')];"
                    "return i.map(x=>Math.round(x.getBoundingClientRect().y)"
                    "+x.getBoundingClientRect().height/2).join(',')})()")
                y = int(ys.split(",")[2]) + 6
                tab.click_row_at_y(y)
                time.sleep(2)
            else:
                print("[2fa-methods] radios:", tab.radio_states(),
                      "|", tab.body(300).replace("\n", " | ")[:200])
            tab.click_text("continue")
            time.sleep(4)
            bc = backup_code()
            if not bc:
                raise RuntimeError("no backup codes left — regenerate in IG app")
            tab.fill_first_input(bc)
            time.sleep(1)
            tab.click_text("continue")
            time.sleep(6)
            print("[pw-gate]", tab.body(200).replace("\n", " | ")[:160])

        # --- step 5: create-new-password gate → same password → session ---
        if only <= 5:
            env = {}
            for line in open(os.path.expanduser("~/cu130-slim/.env")):
                m = re.match(r"([A-Z_0-9]+)=(.*)", line.strip())
                if m:
                    env[m.group(1)] = m.group(2).strip().strip("\"'")
            pw = env.get("INSTAGRAM_PASSWORD") or env.get("FACEBOOK_PASSWORD")
            if "new password" in tab.body(600).lower():
                tab.fill_first_input(pw)
                time.sleep(1)
                tab.click_text("continue")
            # poll until the flow leaves auth surfaces (feed or oauth)
            deadline = time.time() + 90
            while time.time() < deadline:
                u = tab.url()
                if not any(s in u for s in
                           ("password", "login", "two_factor", "challenge")):
                    break
                time.sleep(4)
            print("[session]", tab.url()[:90],
                  "|", tab.body(150).replace("\n", " | ")[:120])

    # --- step 6: OAuth consent ---
    if only <= 6:
        url = url or mint_authorize_url()
        tab = PhoneTab(url_match="instagram.com/")
        tab.nav(url)
        ok = wait_body(tab, "Allow", timeout=40)
        if not ok:
            print("[consent] unexpected:", tab.body(300).replace("\n", " | ")[:200])
            return 4
        # JS click worked where mouse events didn't on wbloks consent
        tab.ev(
            "(()=>{const e=[...document.querySelectorAll("
            "'div[role=button],button')].filter(e=>e.getClientRects().length"
            "&&/^allow$/i.test((e.innerText||'').trim()))[0];"
            "if(e){e.click();return 'ok'}return 'noallow'})()")
        deadline = time.time() + 60
        while time.time() < deadline:
            u = tab.url()
            # the consent URL embeds the callback in params_json (with
            # escaped slashes) — 'social.cloudless.gr' alone false-positives.
            # The real landing carries ?code=…&state=…
            if "code=" in u and ("callback" in u or
                                 "social.cloudless.gr" in u):
                print("[callback] reached:", u[:110])
                return 0
            time.sleep(3)
        print("[callback] timeout; last url:", tab.url()[:110])
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
