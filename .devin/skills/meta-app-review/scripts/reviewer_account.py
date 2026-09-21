#!/usr/bin/env python3
"""Manage the Meta App Review test account for SocialAuto.

Reviewers need working credentials to log in and exercise the app. This
creates a dedicated EDITOR user on the admin (enterprise) team — it sees all
connected accounts/posts but cannot touch admin-only settings. The user gets
NO team of their own, because SocialAuto's team resolution prefers teams
where the user is OWNER; with only the EDITOR membership they land on the
admin team's workspace on first login.

Usage (run from the repo root):

    python3 .devin/skills/meta-app-review/scripts/reviewer_account.py create
    python3 .devin/skills/meta-app-review/scripts/reviewer_account.py verify
    python3 .devin/skills/meta-app-review/scripts/reviewer_account.py status
    python3 .devin/skills/meta-app-review/scripts/reviewer_account.py delete

`create` writes credentials to ~/.socialauto-reviewer-creds.json (mode 600)
and prints the path — put them into the submission's access-code field.
`delete` removes the user and all memberships — run after review concludes.
Never commit the creds file.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REVIEWER_EMAIL = "reviewer@cloudless.gr"
CREDS_PATH = Path.home() / ".socialauto-reviewer-creds.json"

PY_CREATE = r'''
import asyncio, secrets, string
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.user import User, Team, TeamMember, UserRole
from app.core.security import hash_password

EMAIL = "reviewer@cloudless.gr"
pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20)) + "!R9"

async def main():
    async with async_session_maker() as db:
        if (await db.execute(select(User).where(User.email == EMAIL))).scalars().first():
            print("EXISTS")
            return
        team = (await db.execute(
            select(Team).order_by((Team.plan_tier == "enterprise").desc())
        )).scalars().first()
        u = User(email=EMAIL, password_hash=hash_password(pw),
                 name="Meta App Reviewer", timezone="Europe/Athens")
        db.add(u)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=u.id, role=UserRole.EDITOR))
        await db.commit()
        print("CREATED " + pw)

asyncio.run(main())
'''

PY_VERIFY = r'''
import asyncio, json, httpx
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.user import User, TeamMember

EMAIL = "reviewer@cloudless.gr"

async def main():
    async with async_session_maker() as db:
        u = (await db.execute(select(User).where(User.email == EMAIL))).scalars().first()
        if not u:
            print("MISSING")
            return
        ms = (await db.execute(select(TeamMember).where(TeamMember.user_id == u.id))).scalars().all()
        print(f"memberships: {[(str(m.team_id)[:8], m.role.value) for m in ms]}")

asyncio.run(main())
'''

PY_DELETE = r'''
import asyncio
from sqlalchemy import select, delete
from app.db.session import async_session_maker
from app.models.user import User, TeamMember

EMAIL = "reviewer@cloudless.gr"

async def main():
    async with async_session_maker() as db:
        u = (await db.execute(select(User).where(User.email == EMAIL))).scalars().first()
        if not u:
            print("MISSING")
            return
        await db.execute(delete(TeamMember).where(TeamMember.user_id == u.id))
        await db.delete(u)
        await db.commit()
        print("DELETED")

asyncio.run(main())
'''


def run_in_container(code: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python", "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        sys.exit(f"container exec failed:\n{r.stderr.strip()[:400]}")
    return r.stdout.strip()


def verify_login(email: str, password: str) -> str:
    code = f'''
import httpx
r = httpx.post("http://localhost:8000/api/v1/auth/login",
               data={{"username": {email!r}, "password": {password!r}}}, timeout=15)
print("login:", r.status_code)
if r.status_code == 200:
    tok = r.json()["access_token"]
    r2 = httpx.get("http://localhost:8000/api/v1/accounts",
                   headers={{"Authorization": "Bearer " + tok}}, timeout=15)
    body = r2.json()
    print("accounts:", r2.status_code, len(body) if isinstance(body, list) else body)
'''
    return run_in_container(code)


def cmd_create() -> None:
    out = run_in_container(PY_CREATE)
    if out.startswith("EXISTS"):
        print("reviewer account already exists — run `verify` or `delete` first")
        return
    pw = out.removeprefix("CREATED ").strip()
    CREDS_PATH.write_text(json.dumps({"email": REVIEWER_EMAIL, "password": pw}))
    CREDS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"created {REVIEWER_EMAIL} (EDITOR on admin team)")
    print(f"credentials written to {CREDS_PATH} — paste into the Meta")
    print("submission's access-code field + reviewer instructions.")


def cmd_verify() -> None:
    print(run_in_container(PY_VERIFY))
    if not CREDS_PATH.exists():
        print("no creds file — run `create` first (or account was made manually)")
        return
    creds = json.loads(CREDS_PATH.read_text())
    print(verify_login(creds["email"], creds["password"]))


def cmd_status() -> None:
    print(run_in_container(PY_VERIFY))
    print(f"creds file: {'present ' + str(CREDS_PATH) if CREDS_PATH.exists() else 'absent'}")


def cmd_delete() -> None:
    print(run_in_container(PY_DELETE))
    if CREDS_PATH.exists():
        CREDS_PATH.unlink()
        print(f"removed {CREDS_PATH}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"create": cmd_create, "verify": cmd_verify, "status": cmd_status,
     "delete": cmd_delete}.get(cmd, lambda: sys.exit(__doc__))()
