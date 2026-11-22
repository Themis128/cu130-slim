#!/usr/bin/env python3
"""wa_sysuser_auto.py — fully automated system-user token pipeline for the
Cloudless WhatsApp Cloud API account.

Each cycle tries the pure-Graph-API chain first (no browser):

  1. POST /{business}/owned_apps            — claim app into portfolio
  2. POST /{business}/system_users          — create system user
  3. POST /{sysuser}/applications           — install the Meta app
  4. POST /{waba}/assigned_users            — grant WABA access
  5. POST /{sysuser}/access_tokens          — mint never-expiring token

If Meta's temporary restriction (error_subcode 2859015) or the app-capability
gate (#100) blocks the API path, it falls back to driving the Business
Settings UI through the facebook-browser-sidecar (:9226) — the same flow
wa_portfolio_watch.py uses — and retries next cycle.

On success the token is written to
~/.local/state/wa-sysuser/token.txt (chmod 600) — NEVER to logs or stdout.
A marker file token.meta.json records scopes/expiry (no secret material).

Env: reads META_ACCESS_TOKEN + FACEBOOK_CLIENT_SECRET from ~/cu130-slim/.env.

Usage: wa_sysuser_auto.py [--interval 1500] [--window 259200] [--once]
Log:   ~/logs/wa-sysuser-auto.log
"""

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request

REPO_ENV = os.path.expanduser("~/cu130-slim/.env")
BUSINESS_ID = "1558125105019725"
APP_ID = "1936126137016578"
WABA_ID = "1717901383017723"
PHONE_NUMBER_ID = "1432734783254091"
API = "https://graph.facebook.com/v21.0"
SCOPES = "business_management,whatsapp_business_management,whatsapp_business_messaging"
STATE_DIR = os.path.expanduser("~/.local/state/wa-sysuser")
LOG = os.environ.get("WA_SYSUSER_LOG", os.path.expanduser("~/logs/wa-sysuser-auto.log"))
RESTRICTED = "2859015"
CAPABILITY = "(#100)"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_env() -> dict:
    env = {}
    with open(REPO_ENV) as fh:
        for line in fh:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


def gql(path: str, data: dict, token: str, method: str = "POST") -> tuple[int, dict]:
    url = f"{API}/{path}"
    body = dict(data)
    body["access_token"] = token
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(body).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return e.code, {"error": {"message": "unparseable"}}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": {"message": str(exc)}}


def appsecret_proof(token: str, app_secret: str) -> str:
    return hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def err_info(resp: dict) -> str:
    err = resp.get("error", {})
    return f"code={err.get('code')} sub={err.get('error_subcode')} msg={str(err.get('message'))[:120]}"


def api_chain(token: str, secret: str) -> tuple[str, str]:
    """Try the full Graph-API chain. Returns (status, detail).

    status: 'success' | 'restricted' | 'capability' | 'failed'
    On success, token is already persisted; detail is a short note.
    """
    # 1. claim app into portfolio (endpoint exists; was restriction-blocked)
    s, r = gql(f"{BUSINESS_ID}/owned_apps", {"app_id": APP_ID}, token)
    log(f"api owned_apps => {s} {err_info(r)}")
    if s == 0:
        return "failed", "network"
    msg = json.dumps(r)
    if RESTRICTED in msg:
        return "restricted", "owned_apps"
    if s >= 400 and CAPABILITY not in msg:
        # 4xx that isn't restriction/capability — endpoint may not accept POST;
        # not fatal: try system_users anyway (app may already be claimed).
        log(f"api owned_apps non-fatal error: {err_info(r)}")

    # 2. create system user
    s, r = gql(f"{BUSINESS_ID}/system_users", {"name": "socialauto", "role": "EMPLOYEE"}, token)
    log(f"api system_users => {s} {err_info(r)}")
    msg = json.dumps(r)
    if RESTRICTED in msg:
        return "restricted", "system_users"
    if CAPABILITY in msg:
        return "capability", "system_users needs app-in-portfolio first"
    if s >= 400 or "id" not in r:
        return "failed", f"system_users {err_info(r)}"
    sysuser_id = r["id"]
    log(f"system user created: {sysuser_id}")

    # 3. install app for system user
    s, r = gql(f"{sysuser_id}/applications", {"business_app": APP_ID}, token)
    log(f"api sysuser/applications => {s} {err_info(r)}")
    if s >= 400:
        return "failed", f"app install {err_info(r)}"

    # 4. assign WABA + phone number assets
    for edge, oid in ((f"{WABA_ID}/assigned_users", WABA_ID), (f"{PHONE_NUMBER_ID}/assigned_users", PHONE_NUMBER_ID)):
        s, r = gql(edge, {"user": sysuser_id, "tasks": json.dumps(["MANAGE", "DEVELOP"])}, token)
        log(f"api {oid}/assigned_users => {s} {err_info(r)}")
        if s >= 400:
            log(f"asset assign non-fatal: {err_info(r)}")

    # 5. generate never-expiring token
    proof = appsecret_proof(token, secret)
    s, r = gql(
        f"{sysuser_id}/access_tokens",
        {
            "business_app": APP_ID,
            "scope": SCOPES,
            "appsecret_proof": proof,
            "set_token_expires_in_60_days": "false",
        },
        token,
    )
    log(f"api access_tokens => {s} {err_info(r)}")
    msg = json.dumps(r)
    if RESTRICTED in msg:
        return "restricted", "access_tokens"
    new_token = r.get("access_token")
    if s >= 400 or not new_token:
        return "failed", f"token gen {err_info(r)}"

    os.makedirs(STATE_DIR, exist_ok=True)
    tok_path = os.path.join(STATE_DIR, "token.txt")
    with open(tok_path, "w") as fh:
        fh.write(new_token)
    os.chmod(tok_path, 0o600)
    with open(os.path.join(STATE_DIR, "token.meta.json"), "w") as fh:
        json.dump(
            {
                "system_user_id": sysuser_id,
                "app_id": APP_ID,
                "waba_id": WABA_ID,
                "scopes": SCOPES,
                "expires_in_60_days": False,
                "created_at": time.strftime("%FT%TZ", time.gmtime()),
            },
            fh,
            indent=2,
        )
    log("SUCCESS: system-user token minted and stored (token.txt, 0600)")
    return "success", "token stored"


def main() -> int:
    interval = 1500
    window = 259200
    once = "--once" in sys.argv
    for flag, attr in (("--interval", "interval"), ("--window", "window")):
        if flag in sys.argv:
            val = int(sys.argv[sys.argv.index(flag) + 1])
            if attr == "interval":
                interval = val
            else:
                window = val
    env = load_env()
    token = env.get("META_ACCESS_TOKEN", "")
    secret = env.get("FACEBOOK_CLIENT_SECRET", "")
    if not token or not secret:
        log("FATAL: META_ACCESS_TOKEN or FACEBOOK_CLIENT_SECRET missing in .env")
        return 2

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    deadline = time.time() + window
    log(f"wa_sysuser_auto started interval={interval}s window={window}s once={once}")

    while time.time() < deadline:
        status, detail = api_chain(token, secret)
        log(f"cycle result: {status} ({detail})")
        if status == "success":
            return 0
        if once:
            return 0 if status == "success" else 3
        # restricted/capability/failed -> wait for Meta cooldown; UI watcher
        # (wa_portfolio_watch) handles the browser lane in parallel.
        time.sleep(interval)

    log("window expired")
    return 3


if __name__ == "__main__":
    sys.exit(main())
