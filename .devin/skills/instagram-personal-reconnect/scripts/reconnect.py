#!/usr/bin/env python3
"""Full Instagram personal account reconnect flow via instagrapi sidecar.
Tries password login first, falls back to sessionid import if needed.

Usage: reconnect.py <account_id> [sessionid]"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sidecar_healthy  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 2:
    usage("reconnect.py <account_id> [sessionid]")
account_id = sys.argv[1]
sessionid_arg = sys.argv[2] if len(sys.argv) > 2 else ""
script_dir = Path(__file__).resolve().parent


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def run(script: str, *args: str) -> tuple[str, str]:
    """Run sibling script, return (first_stdout_line, rest)."""
    r = subprocess.run([sys.executable, str(script_dir / script), *args],
                       capture_output=True, text=True)
    out = r.stdout.strip().splitlines()
    err(r.stderr.strip())
    return (out[0] if out else "", "\n".join(out[1:]))


err("========================================")
err("  Instagram Personal Reconnect")
err(f"  Account: {account_id}")
err("========================================\n")

err("=== Step 1: Check sidecar ===")
if not sidecar_healthy():
    err("\n❌ Sidecar is not running.")
    err("Start it with: docker compose up -d instagram-private-api")
    sys.exit(1)
err("✅ Sidecar healthy\n")

session_id = ""
if sessionid_arg:
    err("=== Using provided sessionid ===")
    status, rest = run("import-sessionid.py", account_id, sessionid_arg)
    if status.startswith("SESSION_ID:"):
        session_id = status.split(":", 1)[1]
        err("✅ Session imported")
    else:
        err(f"❌ Session import failed: {status}\n{rest}")
        sys.exit(1)
else:
    err("=== Step 2: Login via sidecar ===")
    status, rest = run("login.py", account_id)
    if status.startswith("SESSION_ID:"):
        session_id = status.split(":", 1)[1]
        err("✅ Login successful")
    elif status == "CHALLENGE_REQUIRED":
        err("⚠️ Challenge required. Instagram sent a security code via SMS/email.")
        code = input("Enter the security code: ").strip()
        s2, r2 = run("handle-challenge.py", session_id or "-", rest, code)
        if s2.startswith("SESSION_ID:"):
            session_id = s2.split(":", 1)[1]
            err("✅ Challenge resolved")
        else:
            err(f"❌ Challenge resolution failed: {s2}")
            sys.exit(1)
    elif status == "TWO_FACTOR_REQUIRED":
        err("⚠️ 2FA required. Enter the verification code.")
        code = input("Enter the code: ").strip()
        s2, r2 = run("handle-2fa.py", account_id, code)
        if s2.startswith("SESSION_ID:"):
            session_id = s2.split(":", 1)[1]
            err("✅ 2FA verified")
        else:
            err(f"❌ 2FA verification failed: {s2}")
            sys.exit(1)
    elif status == "ERROR:UnknownError":
        err("⚠️ Password login blocked by Instagram version check.")
        err("Falling back to sessionid import...\n")
        err("Please provide the Instagram sessionid cookie:")
        err("  - Open instagram.com in a logged-in browser")
        err("  - DevTools → Application → Cookies → sessionid")
        err("  - Or use the Playwright MCP browser\n")
        sessionid_cookie = input("Sessionid: ").strip()
        if not sessionid_cookie:
            err("❌ No sessionid provided. Aborting.")
            sys.exit(1)
        s2, r2 = run("import-sessionid.py", account_id, sessionid_cookie)
        if s2.startswith("SESSION_ID:"):
            session_id = s2.split(":", 1)[1]
            err("✅ Session imported")
        else:
            err(f"❌ Session import failed: {s2}")
            sys.exit(1)
    elif status.startswith("ERROR:"):
        err(f"❌ Login failed: {status}\n{rest}")
        sys.exit(1)
    else:
        err(f"❌ Unexpected login result: {status}\n{rest}")
        sys.exit(1)

err("\n=== Step 3: Save session ===")
run("save-session.py", account_id, session_id)

err("\n=== Step 4: Verify session ===")
rc = subprocess.run([sys.executable, str(script_dir / "verify.py"), account_id]).returncode
err("")
err("========================================")
if rc == 0:
    err("  ✅ Instagram personal account reconnected")
else:
    err("  ⚠️ Session saved but verification failed")
    err("  The session may need a few minutes to propagate.")
err("========================================")
sys.exit(rc)
