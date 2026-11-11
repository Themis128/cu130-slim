#!/usr/bin/env python3
"""Instagram session recovery — password-login → private_api session sync.

Automates the 2026-11-11 recovery that healed IG publishing after Meta
invalidated the Graph token (OAuthException 190) AND browser sessionids were
rejected by the private mobile API (`login_required` redirect loop).

Root cause (upstream): instagrapi #2920 — Instagram killed `qe/sync/` on
2026-11-03; aiograpi >= 2.1.3 fetches the password-encryption key from
`launcher/sync/` instead. Browser/web sessionids are NOT accepted by the
private API — the supported path is ONE password login, dump settings,
reuse the resulting sessionid.

What this script does, end to end:
  1. Runs `aiograpi.Client.login()` inside the `instagram-private-api`
     container using INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD (from
     ~/cu130-slim/.env or container env).
  2. Dumps settings to /app/ig_session.json AND copies to the
     `instagram_sessions` volume at /data/ig_session.json (survives
     container recreate).
  3. Extracts `sessionid` + `ds_user_id` and writes them ENCRYPTED into
     `social_accounts.meta_data.private_api_session_id` /
     `private_api_ds_user_id` (fields the publish ladder actually reads),
     clears stale `private_api_csrf_token`, restores status='active'.
  4. Requeues IG post_targets stuck `failed/terminal` with auth-revoked
     errors back to `transient` so publish-retry-sweep republishes them.

Usage:
    python3 ig_session_recover.py            # full recovery
    python3 ig_session_recover.py --dry-run  # login + report, no DB writes

Requirements: docker access, ~/cu130-slim/.env with INSTAGRAM_USERNAME /
INSTAGRAM_PASSWORD, social-api + instagram-private-api containers up.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = os.path.expanduser("~/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")
IG_CONTAINER = "instagram-private-api"
API_CONTAINER = "social-api"
SETTINGS_CONTAINER_PATH = "/app/ig_session.json"
SETTINGS_VOLUME_PATH = "/data/ig_session.json"

# Runs INSIDE instagram-private-api. Prints LOGIN_OK <uid> or raises.
_LOGIN_SNIPPET = r"""
import asyncio, os, sys
from aiograpi import Client
async def m():
    c = Client()
    await c.login(os.environ["IGU"], os.environ["IGP"])
    c.dump_settings("/app/ig_session.json")
    print("LOGIN_OK", c.user_id)
asyncio.run(m())
"""

# Runs INSIDE instagram-private-api: emit sessionid + ds_user_id as JSON.
_EXTRACT_SNIPPET = r"""
import json, re
s = open("/app/ig_session.json").read()
sid = re.search(r'"sessionid"\s*:\s*"([^"]+)"', s)
ds = re.search(r'"ds_user_id"\s*:\s*"?([0-9]+)', s)
print(json.dumps({"sessionid": sid.group(1) if sid else None,
                  "ds_user_id": ds.group(1) if ds else None}))
"""

# Runs INSIDE social-api: encrypt + persist into social_accounts.meta_data.
_DB_SYNC_SNIPPET = r"""
import asyncio, os, json
from sqlalchemy import select, update
from app.db.session import async_session_maker
from app.core.security import encrypt_field
from app.models.social_account import SocialAccount
async def m():
    async with async_session_maker() as s:
        rows = (await s.execute(
            select(SocialAccount).where(SocialAccount.platform == "instagram")
        )).scalars().all()
        for a in rows:
            md = dict(a.meta_data or {})
            md["private_api_session_id"] = encrypt_field(os.environ["SID"])
            if os.environ.get("DS"):
                md["private_api_ds_user_id"] = os.environ["DS"]
            md.pop("private_api_csrf_token", None)
            await s.execute(update(SocialAccount)
                .where(SocialAccount.id == a.id)
                .values(meta_data=md, status="active"))
            print("updated", a.username)
        await s.commit()
asyncio.run(m())
"""


def _env_credentials() -> tuple[str, str]:
    user = pw = ""
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            if line.startswith("INSTAGRAM_USERNAME="):
                user = line.split("=", 1)[1].strip()
            elif line.startswith("INSTAGRAM_PASSWORD="):
                pw = line.split("=", 1)[1].strip()
    # container env wins if .env lacks them
    if not user or not pw:
        out = subprocess.run(
            ["docker", "exec", IG_CONTAINER, "env"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        for line in out.splitlines():
            if line.startswith("INSTAGRAM_USERNAME="):
                user = user or line.split("=", 1)[1].strip()
            elif line.startswith("INSTAGRAM_PASSWORD="):
                pw = pw or line.split("=", 1)[1].strip()
    if not user or not pw:
        sys.exit("INSTAGRAM_USERNAME/PASSWORD not found in .env or container env")
    return user, pw


def _docker_exec(container: str, code: str, env: dict | None = None,
                 timeout: int = 180) -> str:
    cmd = ["docker", "exec"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += ["-w", "/app", container, "python", "-c", code]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        sys.exit(f"docker exec {container} failed:\n{r.stderr[-2000:]}")
    return r.stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="login + extract only; skip DB sync and requeue")
    args = ap.parse_args()

    user, pw = _env_credentials()

    # 1-2. password login + dump settings (container + persistent volume)
    print("[*] aiograpi password login ...")
    out = _docker_exec(IG_CONTAINER, _LOGIN_SNIPPET,
                       env={"IGU": user, "IGP": pw}, timeout=240)
    print("   ", out.strip().splitlines()[-1] if out.strip() else "no output")
    if "LOGIN_OK" not in out:
        sys.exit("login did not report LOGIN_OK")

    subprocess.run(
        ["docker", "exec", IG_CONTAINER, "sh", "-c",
         f"cp {SETTINGS_CONTAINER_PATH} {SETTINGS_VOLUME_PATH}"],
        check=True, timeout=30,
    )
    print(f"[*] settings persisted -> {SETTINGS_VOLUME_PATH}")

    # 3. extract session identifiers
    raw = _docker_exec(IG_CONTAINER, _EXTRACT_SNIPPET, timeout=60).strip()
    ids = json.loads(raw.splitlines()[-1])
    if not ids.get("sessionid"):
        sys.exit("no sessionid found in dumped settings")
    print(f"[*] sessionid for ds_user_id={ids.get('ds_user_id')}")

    if args.dry_run:
        print("[dry-run] skipping DB sync + requeue")
        return

    # 4. encrypt + persist into social_accounts.meta_data
    print("[*] syncing private_api session into social_accounts ...")
    _docker_exec(API_CONTAINER, _DB_SYNC_SNIPPET,
                 env={"SID": ids["sessionid"],
                      "DS": ids.get("ds_user_id") or ""}, timeout=120)

    # 5. requeue auth-revoked terminal IG targets -> transient for the sweep
    print("[*] requeueing auth-revoked IG targets ...")
    sql = (
        "update post_targets pt set error_class='transient', "
        "error_message=left(replace(pt.error_message,'auto-retries stopped','auto-retries resumed'),400), "
        "updated_at=now() from social_accounts sa "
        "where pt.social_account_id=sa.id and sa.platform='instagram' "
        "and pt.status='failed' and pt.error_class='terminal' "
        "and pt.error_message ilike '%auth revoked%';"
    )
    r = subprocess.run(
        ["docker", "exec", "social-postgres", "psql", "-U", "social_user",
         "-d", "social_automation", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    print("   ", (r.stdout or r.stderr).strip().splitlines()[-1])

    print("[+] Instagram session recovered. publish-retry-sweep will republish.")


if __name__ == "__main__":
    main()
