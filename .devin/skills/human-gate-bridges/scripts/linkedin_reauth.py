#!/usr/bin/env python3
"""linkedin_reauth.py — automate the LinkedIn sidecar re-auth lane.

Drives POST /login on linkedin-browser-sidecar (:9225). When LinkedIn
answers two_factor_required, polls the Galaxy S22+ SMS inbox over ADB
(Windows adb.exe — WSL adb is blind to the USB device) for the LinkedIn
verification code and retries with verification_code. Falls back to
Termux SMS over SSH, then exits asking for a manual code.

    linkedin_reauth.py            # full auto lane
    linkedin_reauth.py <code>     # skip SMS poll, submit this code

Credentials come from the repo .env (LINKEDIN_EMAIL / LINKEDIN_PASSWORD)
— never printed. Safe to re-run: /login starts a clean cookie-free
context, and a successful login persists storageState to the sidecar
volume.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

SIDECAR = os.environ.get("LINKEDIN_SIDECAR_URL", "http://127.0.0.1:9225")
REPO = os.environ.get("REPO", os.path.expanduser("~/cu130-slim"))
ENV_FILE = os.path.join(REPO, ".env")

ADBS = [
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
    "adb",
]
PHONE_IP = os.environ.get("PHONE_SSH_HOST", "100.84.31.85")

# LinkedIn verification SMS: 6-8 digit code, sender/body mentions LinkedIn.
LI_CODE_RE = re.compile(r"\b(\d{6,8})\b")
LI_HINT_RE = re.compile(r"linkedin", re.I)


def env_val(key: str) -> str:
    try:
        for line in open(ENV_FILE):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get(key, "")


def post(path: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        SIDECAR + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"error": str(e)}


def get(path: str, timeout: int = 120) -> dict:
    try:
        with urllib.request.urlopen(SIDECAR + path, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def _sms_rows(text: str) -> list[str]:
    return [l for l in text.splitlines() if LI_HINT_RE.search(l)]


def adb_li_code() -> str | None:
    for adb in ADBS:
        try:
            r = subprocess.run(
                [adb, "shell", "content", "query", "--uri", "content://sms/inbox",
                 "--projection", "address,date,body", "--sort", "date DESC"],
                capture_output=True, text=True, timeout=10)
            rows = _sms_rows(r.stdout)
            # newest LinkedIn SMS first
            for row in rows[:5]:
                m = LI_CODE_RE.search(row)
                if m:
                    return m.group(1)
        except Exception:
            continue
    return None


def termux_li_code() -> str | None:
    try:
        r = subprocess.run(
            ["ssh", "-p", "8022", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no",
             f"u0_a357@{PHONE_IP}", "termux-sms-list -l 10"],
            capture_output=True, text=True, timeout=15)
        for row in _sms_rows(r.stdout)[:5]:
            m = LI_CODE_RE.search(row)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def wait_for_sms_code(seen_before: set, timeout_s: int = 240) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for fn in (adb_li_code, termux_li_code):
            code = fn()
            if code and code not in seen_before:
                return code
        time.sleep(8)
    return None


def snapshot_sms_codes() -> set:
    """Codes already in the inbox — LinkedIn may resend an old-looking
    code; only accept one that arrives AFTER we trigger login."""
    codes = set()
    for fn in (adb_li_code, termux_li_code):
        c = fn()
        if c:
            codes.add(c)
    return codes


def main() -> int:
    user, pw = env_val("LINKEDIN_EMAIL"), env_val("LINKEDIN_PASSWORD")
    if not (user and pw):
        sys.exit("LINKEDIN_EMAIL/LINKEDIN_PASSWORD not in .env")

    manual_code = sys.argv[1] if len(sys.argv) > 1 else None
    pre_codes = snapshot_sms_codes()

    res = post("/login", {"username": user, "password": pw})
    print("login attempt 1:", {k: v for k, v in res.items()
                             if k != "storage_state"})
    if res.get("logged_in"):
        print("LOGIN OK — no 2FA needed")
    elif res.get("two_factor_required"):
        code = manual_code or wait_for_sms_code(pre_codes)
        if not code:
            sys.exit("2FA required but no LinkedIn SMS code found — "
                     "rerun: linkedin_reauth.py <code>")
        print("submitting verification code")
        res = post("/login",
                   {"username": user, "password": pw,
                    "verification_code": code})
        print("login attempt 2:", {k: v for k, v in res.items()
                                 if k != "storage_state"})
        if not res.get("logged_in"):
            sys.exit("2FA code rejected or login still failing")
    else:
        sys.exit(f"login failed: {res.get('error') or res.get('message')}")

    # Verify: /session navigates a fresh check to /feed/.
    sess = get("/session", timeout=180)
    print("session check:", sess)
    return 0 if sess.get("logged_in") else 1


if __name__ == "__main__":
    sys.exit(main())
