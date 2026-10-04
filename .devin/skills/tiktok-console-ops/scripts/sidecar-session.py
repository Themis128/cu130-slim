#!/usr/bin/env python3
"""TikTok browser sidecar session helpers.
Usage: sidecar-session.py status|restore|qr|ensure"""

import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (NODE_WORK, OUT_DIR, SIDECAR, ensure_playwright,  # noqa: E402
                     env_key, run_mjs)
from skill_http import usage  # noqa: E402


def _post(path: str, body: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        f"{SIDECAR}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def _get(path: str, timeout: int = 30) -> dict:
    return json.loads(urllib.request.urlopen(f"{SIDECAR}{path}", timeout=timeout).read().decode())


def status() -> None:
    for path in ("/health", "/session"):
        try:
            with urllib.request.urlopen(f"{SIDECAR}{path}", timeout=10) as r:
                print(json.dumps(json.loads(r.read().decode()), indent=2))
        except Exception:
            pass


def restore() -> None:
    """Re-inject the real cookie set stored in social_accounts meta_data
    ->'tiktok_web_cookies'. Fastest recovery path — those cookies stay valid
    for months."""
    query = (
        "SELECT meta_data->'tiktok_web_cookies' FROM social_accounts "
        "WHERE platform ILIKE 'tiktok' LIMIT 1;"
    )
    out = subprocess.run(
        ["docker", "exec", "social-postgres", "psql", "-U", "social_user",
         "-d", "social_automation", "-t", "-A", "-c", query],
        capture_output=True, text=True, timeout=30)
    if out.returncode != 0 or not out.stdout.strip():
        print("No tiktok_web_cookies in social_accounts", file=sys.stderr)
        sys.exit(1)
    ck = json.loads(out.stdout.strip())
    cookies = {k: v for k, v in ck.items() if isinstance(v, str) and v}
    session_id = cookies.pop("sessionid", None)
    print(json.dumps(_post("/session", {"session_id": session_id,
                                        "cookies": cookies}), indent=2))


def qr(out_path: str = "/tmp/tiktok-qr.png", wait: int = 150) -> None:
    """Native sidecar QR login — POST /login/qr, write the PNG, poll status.
    NOTE: TikTok usually rejects QR confirms from headless contexts — prefer
    `restore` or the headed :9223 bridge QR flow (see SKILL.md)."""
    resp = _post("/login/qr", {}, timeout=90)
    if "qr_png_b64" not in resp:
        print(json.dumps(resp, indent=2))
        sys.exit(1)
    Path(out_path).write_bytes(base64.b64decode(resp["qr_png_b64"]))
    print(f"QR written to {out_path} — scan with TikTok app, then Confirm")
    deadline = time.time() + wait
    while time.time() < deadline:
        st = _get("/login/qr/status")
        if st.get("logged_in"):
            print(json.dumps(st, indent=2))
            return
        time.sleep(5)
    print("Timed out waiting for QR confirmation", file=sys.stderr)
    sys.exit(2)


action = sys.argv[1] if len(sys.argv) > 1 else "status"
if action == "status":
    status()
elif action == "restore":
    restore()
elif action == "qr":
    qr(*(sys.argv[2:3] or []))
elif action == "ensure":
    NODE_WORK.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_playwright()
    if not env_key("TIKTOK_DEV_EMAIL") or not env_key("TIKTOK_DEV_PASSWORD"):
        print("Need TIKTOK_DEV_EMAIL and TIKTOK_DEV_PASSWORD in .env",
              file=sys.stderr)
        sys.exit(1)
    rc = run_mjs("ensure-session.mjs", extra_env={"TIKTOK_SIDECAR_URL": SIDECAR})
    status()
    sys.exit(rc)
else:
    usage("sidecar-session.py status|restore|qr|ensure")
