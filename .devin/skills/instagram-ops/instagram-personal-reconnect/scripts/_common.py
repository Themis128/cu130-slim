#!/usr/bin/env python3
"""Shared helpers for instagram-personal-reconnect scripts."""

import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, env, request, social_api  # noqa: E402

SIDECAR = "http://localhost:8011"


def sidecar_get(path: str, session_id: str = ""):
    req = urllib.request.Request(
        f"{SIDECAR}{path}",
        headers={"X-Session-ID": session_id} if session_id else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()
    except urllib.error.URLError:
        return ""


def sidecar_post(path: str, data: dict, session_id: str = "", json_body: bool = False):
    if json_body:
        body = json.dumps(data).encode()
        ctype = "application/json"
    else:
        body = urllib.parse.urlencode(data).encode()
        ctype = "application/x-www-form-urlencoded"
    headers = {"Content-Type": ctype}
    if session_id:
        headers["X-Session-ID"] = session_id
    req = urllib.request.Request(f"{SIDECAR}{path}", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode()
    except urllib.error.URLError as e:
        return e.read().decode() if hasattr(e, "read") else str(e)


def sidecar_healthy() -> bool:
    resp = sidecar_get("/health")
    if not resp:
        return False
    try:
        d = json.loads(resp)
        return d.get("status") in ("ok", "healthy", True) or d.get("healthy") is True
    except ValueError:
        return "ok" in resp


def get_credentials(account_id: str) -> tuple[str, str]:
    """Return (username, password) for an Instagram account."""
    api, token = social_api()
    info = request("GET", f"{api}/api/v1/accounts", token=token)
    accounts = info if isinstance(info, list) else info.get("accounts", info.get("data", []))
    acct = next((a for a in accounts if a.get("id") == account_id), {})
    username_key = ("INSTAGRAM_USERNAME_T_BALTZAKIS"
                    if acct.get("account_type") == "personal" else "INSTAGRAM_USERNAME")
    username = ""
    try:
        username = request("GET", f"{api}/api/v1/secrets/{username_key}",
                           token=token).get("value", "")
    except SystemExit:
        pass
    if not username:
        username = acct.get("username") or acct.get("display_name", "")
    password = ""
    try:
        password = request("GET", f"{api}/api/v1/secrets/INSTAGRAM_PASSWORD",
                           token=token).get("value", "")
    except SystemExit:
        pass
    if not password:
        password = env("INSTAGRAM_PASSWORD")
    if not username:
        print(f"❌ Could not find username for account {account_id}", file=sys.stderr)
        sys.exit(1)
    if not password:
        print("❌ Could not retrieve password from secret store or .env", file=sys.stderr)
        sys.exit(1)
    return username, password


def parse_login_result(raw: str) -> tuple[str, str]:
    """Return (status_line, detail) — status_line is SESSION_ID:x / CHALLENGE_REQUIRED /
    TWO_FACTOR_REQUIRED / ERROR:x / UNKNOWN."""
    try:
        d = json.loads(raw)
    except ValueError:
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            return "SESSION_ID:" + json.loads(raw), ""
        return "ERROR:empty_response", raw
    if isinstance(d, str):
        return "SESSION_ID:" + d, ""
    if not isinstance(d, dict):
        return "UNKNOWN", raw
    if d.get("session_id"):
        return "SESSION_ID:" + d["session_id"], ""
    if d.get("status") == "ok":
        return "RESOLVED", ""
    err = d.get("error")
    if err == "challenge_required":
        return "CHALLENGE_REQUIRED", json.dumps(d.get("last_json", {}))
    if err == "two_factor_required":
        return "TWO_FACTOR_REQUIRED", json.dumps(d.get("last_json", {}))
    if err:
        return f"ERROR:{err}", str(d.get("message", d.get("detail", "")))
    if d.get("detail") and d.get("exc_type"):
        return f"ERROR:{d.get('exc_type', 'unknown')}", str(d.get("detail", ""))
    return "UNKNOWN", json.dumps(d)


def psql(sql: str, tuples_only: bool = False) -> str:
    cmd = ["docker", "compose", "exec", "-T", "social-postgres",
           "psql", "-U", "social_user", "-d", "social_automation"]
    if tuples_only:
        cmd.append("-t")
    cmd += ["-c", sql]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
