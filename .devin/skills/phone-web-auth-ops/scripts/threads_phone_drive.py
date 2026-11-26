#!/usr/bin/env python3
"""threads_phone_drive.py — Threads re-mint on phone Chrome (SSO lane).

Full Threads OAuth re-mint when the IG session on phone Chrome is live
but the Threads session is cold: clear threads cookies -> threads.com
login extracts the instagram.com OIDC `oidc_url` -> SSO bridge profile
card -> GDPR "Continue As" consent -> threads OAuth authorize ->
callback. Verified live 2026-11-26 (token minted, expires 2027-01-25).

Usage (run from WSL, phone unlocked + Chrome foregrounded):
    threads_phone_drive.py --authorize-url URL
    threads_phone_drive.py --mint threads        # mint via social-api

--authorize-url should be the /api/v1/auth/oauth/threads/authorize URL
(phone_oauth_drive.py --mint threads prints one). Exit codes:
0 callback reached / 3 no oidc_url (IG session dead — run
ig_phone_credential_login.py first) / 4 timeout. Never prints codes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

REPO = os.path.expanduser("~/cu130-slim")
sys.path.insert(0, os.path.join(
    REPO, ".devin/skills/phone-web-auth-ops/scripts"))
import phone_cdp  # noqa: E402
from phone_oauth_drive import mint  # noqa: E402

COOKIES = ["sessionid", "ds_user_id", "csrftoken", "ig_did", "mid",
           "dpr", "datr", "fr", "sb"]
DOMAINS = [".threads.com", ".threads.net", "threads.com", "threads.net",
           "www.threads.com", "www.threads.net"]

CARD_JS = """(()=>{
const els=[...document.querySelectorAll('div[role=button],button,a')]
.filter(e=>e.getClientRects().length&&/continue to threads/i.test(e.innerText||''));
if(els.length){els[0].click();return 'card:'+els[0].innerText.slice(0,60)}
const cards=[...document.querySelectorAll('div[role=button]')]
.filter(e=>e.getClientRects().length&&(e.innerText||'').includes('@'));
if(cards.length){cards[0].click();return 'atcard:'+cards[0].innerText.slice(0,60)}
return 'none'})()"""

CONSENT_JS = """(()=>{const els=[...document.querySelectorAll('div[role=button],button')]
.filter(e=>e.getClientRects().length&&/continue as|^allow$|authorize/i.test((e.innerText||'').trim()));
if(els.length){els[0].click();return 'clicked:'+els[0].innerText.trim().slice(0,40)}return 'none'})()"""


def pick_tab():
    tabs = phone_cdp.tabs()
    for t in tabs:
        if "threads.com" in t.get("url", ""):
            return t
    return tabs[-1] if tabs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--authorize-url")
    ap.add_argument("--mint", choices=["threads"])
    args = ap.parse_args()
    auth = args.authorize_url or (mint(args.mint) if args.mint else None)
    if not auth:
        ap.error("need --authorize-url or --mint threads")

    phone_cdp.forward()
    phone_cdp.front()
    tid = phone_cdp.new_tab("https://www.threads.com/login/")
    time.sleep(6)
    t = next((x for x in phone_cdp.tabs() if x["id"] == tid), None) or pick_tab()
    if not t:
        print("no tabs on phone Chrome")
        return 3
    tab = phone_cdp.Tab(t["webSocketDebuggerUrl"])
    tab.ws.settimeout(25)
    ev = lambda e: tab.evaluate(e)

    # step 1: clear threads cookies (IG cookies must survive — SSO needs them)
    tab.call("Network.enable")
    for name in COOKIES:
        for dom in DOMAINS:
            try:
                tab.call("Network.deleteCookies",
                         {"name": name, "domain": dom}, retries=1)
            except Exception:
                pass
    print("[cookies] cleared")

    # step 2: login page -> extract instagram oidc_url -> SSO bridge
    tab.navigate("https://www.threads.com/login/")
    time.sleep(8)
    html = ev("document.documentElement.innerHTML") or ""
    m = re.search(r'"oidc_url"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if not m:
        print("[oidc] no oidc_url; url=", (ev("location.href") or "")[:120])
        print("BODY:", (ev("document.body.innerText.slice(0,300)") or "")[:200])
        return 3
    oidc = json.loads('"' + m.group(1) + '"')
    print("[oidc] got url:", oidc[:110])

    tab.navigate(oidc)
    time.sleep(8)
    for i in range(15):
        u = ev("location.href") or ""
        b = ev("document.body.innerText.slice(0,400)") or ""
        print(f"[sso {i}] {u[:100]} | {b.replace(chr(10), ' | ')[:140]}")
        if "threads.com" in u and "login" not in u and "sso" not in u:
            break
        print("   click:", ev(CARD_JS))
        time.sleep(5)

    # step 3: OAuth consent -> callback
    tab.navigate(auth)
    ok = False
    for i in range(20):
        time.sleep(4)
        u = ev("location.href") or ""
        b = ev("document.body.innerText.slice(0,500)") or ""
        print(f"[auth {i}] {u[:110]} | {b.replace(chr(10), ' | ')[:160]}")
        if "callback" in u:
            ok = True
            break
        if re.search(r"continue as|allow|authorize", b, re.I):
            print("   consent:", ev(CONSENT_JS))
    print("FINAL:", (ev("location.href") or "")[:160])
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
