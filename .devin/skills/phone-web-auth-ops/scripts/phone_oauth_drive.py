#!/usr/bin/env python3
"""phone_oauth_drive.py — generic OAuth consent drive on phone Chrome.

Drives any SocialAuto OAuth authorize URL through the phone's live
session: navigate -> handle consent gates (Allow / Continue As X /
GDPR continue) -> land on the platform callback -> report.

Complements threads_phone_drive.py (Threads-specific SSO prelude) and
ig_phone_reauth.py (IG recovery flow): use this when the platform
session is ALREADY live on phone Chrome and only the consent +
callback remain. Verified live 2026-11-26 for instagram2 + threads.

Usage:
    phone_oauth_drive.py --authorize-url URL [--tab-match SUBSTR]
    phone_oauth_drive.py --mint instagram2|threads   # mint via social-api

Exit codes: 0 callback reached / 3 login-wall (session dead — run the
platform's session-recovery lane first) / 4 timeout / 5 usage.
Never prints tokens, codes, or secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.expanduser("~/cu130-slim")
sys.path.insert(0, os.path.join(
    REPO, ".devin/skills/phone-web-auth-ops/scripts"))
import phone_cdp  # noqa: E402

CALLBACK_RE = re.compile(r"/api/v1/auth/oauth/[^/?]+/callback")
LOGIN_WALL_RE = re.compile(r"accounts/login|/login\?|checkpoint|challenge",
                           re.I)

MINT_SNIPPET = r'''
import asyncio, os, httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import create_access_token
async def main():
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.connect() as c:
        uid = await c.scalar(text("select id from users order by created_at limit 1"))
    t = create_access_token({"sub": str(uid)})
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.get("http://localhost:8000/api/v1/auth/oauth/%s/authorize",
                        params={"team_id": "10024d25-2ea4-40ea-86c6-ba89f515a2eb"},
                        headers={"Authorization": "Bearer " + t})
        print(r.json().get("authorization_url", ""))
asyncio.run(main())
'''


def mint(platform: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "-w", "/app", "-e", "PYTHONPATH=/app",
         "social-api", "python3", "-c", MINT_SNIPPET % platform],
        capture_output=True, text=True, timeout=90)
    for line in r.stdout.splitlines():
        if line.startswith("http"):
            return line.strip()
    raise RuntimeError(f"mint failed: {r.stdout[-200:]} {r.stderr[-200:]}")


def consent_click_js() -> str:
    return """(()=>{
const els=[...document.querySelectorAll('div[role=button],button,a,[role=link]')]
.filter(e=>e.getClientRects().length&&/continue as|^allow$|^continue$|authorize|agree/i
.test((e.innerText||'').trim()));
if(!els.length)return 'none';
els[els.length-1].click();
return 'clicked:'+(els[els.length-1].innerText||'').trim().slice(0,50)})()"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--authorize-url")
    ap.add_argument("--mint", choices=["instagram2", "threads", "facebook"])
    ap.add_argument("--tab-match", default="")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    url = args.authorize_url or (mint(args.mint) if args.mint else None)
    if not url:
        ap.error("need --authorize-url or --mint")

    phone_cdp.forward()
    phone_cdp.front()
    # fresh foreground tab — background tabs freeze CDP evals on Android
    tid = phone_cdp.new_tab(url)
    time.sleep(6)
    tabs = phone_cdp.tabs()
    t = next((x for x in tabs if x["id"] == tid), None) or tabs[-1]
    tab = phone_cdp.Tab(t["webSocketDebuggerUrl"])
    tab.ws.settimeout(25)
    ev = lambda e: tab.evaluate(e)

    deadline = time.time() + args.timeout
    last_body = ""
    while time.time() < deadline:
        u = ev("location.href") or ""
        if CALLBACK_RE.search(u):
            print("[callback]", u[:140])
            print("BODY:", (ev("document.body.innerText.slice(0,300)") or "")[:200])
            return 0
        if LOGIN_WALL_RE.search(u):
            print("[login-wall]", u[:140])
            print("BODY:", (ev("document.body.innerText.slice(0,300)") or "")[:200])
            return 3
        b = ev("document.body.innerText.slice(0,500)") or ""
        last_body = b
        print(f"[poll] {u[:100]} | {b.replace(chr(10),' | ')[:120]}")
        if re.search(r"continue as|^allow$|^continue$|would you like to continue|"
                     r"previously connected", b, re.I):
            print("   consent:", ev(consent_click_js()))
        time.sleep(4)

    print("[timeout] last url:", (ev("location.href") or "")[:140])
    print("BODY:", last_body.replace("\n", " | ")[:250])
    return 4


if __name__ == "__main__":
    sys.exit(main())
