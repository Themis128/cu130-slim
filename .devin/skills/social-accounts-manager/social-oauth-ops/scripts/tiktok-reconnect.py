#!/usr/bin/env python3
"""Reconnect the TikTok SocialAuto account through OAuth using the stored
tiktok.com web session. Needed to grant NEW scopes (e.g. user.info.stats)
— token refresh can only renew, never expand, granted scopes.

Usage:
  tiktok-reconnect.py [--dry-run] [username]

Prereqs: social-api up, SOCIAL_ADMIN_* in .env, tiktok_web_cookies stored
on the account (QR login — see tiktok-console-ops skill), Playwright image.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, repo_root, request  # noqa: E402

ROOT = repo_root()
PW_WORK = ROOT / ".cursor/tmp-tiktok-pw/node_work"
PW_OUT = ROOT / ".cursor/tmp-tiktok-pw/out"
PW_WORK.mkdir(parents=True, exist_ok=True)
PW_OUT.mkdir(parents=True, exist_ok=True)

DRY_RUN = "--dry-run" in sys.argv[1:]
positional = [a for a in sys.argv[1:] if not a.startswith("--")]
TIKTOK_USERNAME = env("TIKTOK_USERNAME", positional[0] if positional else "")

EXPORT_PY = '''
import asyncio, json, os
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select

async def m():
    async with async_session_maker() as s:
        q = select(SocialAccount).where(
            SocialAccount.platform == "tiktok",
            SocialAccount.status == "active",
        )
        uname = os.environ.get("TIKTOK_USERNAME") or ""
        if uname:
            q = q.where(SocialAccount.username == uname.lstrip("@"))
        accs = (await s.execute(q)).scalars().all()
        if not accs:
            raise SystemExit("no active tiktok account found")
        acc = accs[0]
        if len(accs) > 1:
            print(f"note: {len(accs)} tiktok accounts — using @{acc.username}; "
                  "pass TIKTOK_USERNAME to pick another")
        cookies = (acc.meta_data or {}).get("tiktok_web_cookies") or {}
        if not cookies:
            raise SystemExit("no tiktok_web_cookies on the account — "
                             "do the QR login first (tiktok-console-ops)")
        out = [{"name": k, "value": v, "domain": ".tiktok.com",
                "path": "/", "secure": True} for k, v in cookies.items()]
        open("/tmp/tt_cookies.json", "w").write(json.dumps(out))
        open("/tmp/tt_team_id.txt", "w").write(str(acc.team_id))
        print(f"wrote {len(out)} cookies for @{acc.username}")

asyncio.run(m())
'''

SCOPES_PY = '''
import asyncio
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select

async def m():
    async with async_session_maker() as s:
        acc = (await s.execute(select(SocialAccount).where(
            SocialAccount.platform == "tiktok"))).scalars().first()
        print("granted scopes:", ", ".join(acc.scopes or []))

asyncio.run(m())
'''

cookies_path = PW_WORK / "tt_cookies.json"
try:
    if not (PW_WORK / "node_modules/playwright").exists():
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{PW_WORK}:/work", "-w", "/work",
             "mcr.microsoft.com/playwright:v1.62.1",
             "bash", "-lc",
             "npm init -y >/dev/null && npm i playwright@1.62.1 --no-fund --no-audit"],
            check=True)

    # 1. Export stored tiktok.com cookies + team_id
    subprocess.run(
        ["docker", "exec", "-i", "-e", f"TIKTOK_USERNAME={TIKTOK_USERNAME}",
         "social-api", "python", "-c", EXPORT_PY], check=True)
    subprocess.run(
        ["docker", "cp", "social-api:/tmp/tt_cookies.json",
         str(cookies_path)], check=True)
    team_id = subprocess.run(
        ["docker", "exec", "social-api", "cat", "/tmp/tt_team_id.txt"],
        capture_output=True, text=True, check=True).stdout.strip()

    # 2. Fresh authorize URL (embeds team_id + PKCE verifier in state)
    email, password = env("SOCIAL_ADMIN_EMAIL"), env("SOCIAL_ADMIN_PASSWORD")
    token_resp = request(
        "POST", "http://127.0.0.1:8083/api/v1/auth/login",
        form={"username": email, "password": password})
    token = token_resp["access_token"]
    conn = request(
        "POST", "http://127.0.0.1:8083/api/v1/accounts/connect",
        token=token,
        data={"platform": "tiktok", "team_id": team_id})
    auth_url = conn.get("authorization_url") or conn.get("authorize_url") or conn.get("url")
    if not auth_url:
        raise SystemExit(f"could not get authorize URL: {conn}")
    print("authorize URL obtained")

    # 3. Drive consent headlessly
    shutil.copy(Path(__file__).resolve().parent / "tiktok-oauth.mjs",
                PW_WORK / "tiktok-oauth.mjs")
    env_vars = {"TT_AUTH_URL": auth_url, "OUT_DIR": "/out",
                "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright"}
    if DRY_RUN:
        env_vars["TT_DRY_RUN"] = "1"
    cmd = ["docker", "run", "--rm", "--network", "host"]
    for k, v in env_vars.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += ["-v", f"{PW_WORK}:/work", "-v", f"{PW_OUT}:/out", "-w", "/work",
            "mcr.microsoft.com/playwright:v1.62.1",
            "node", "/work/tiktok-oauth.mjs"]
    subprocess.run(cmd, check=True)

    # 4. Report granted scopes
    subprocess.run(
        ["docker", "exec", "-i", "social-api", "python", "-c", SCOPES_PY],
        check=True)
finally:
    # Never leave live session cookies in the shared work dir.
    cookies_path.unlink(missing_ok=True)
