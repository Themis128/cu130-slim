#!/usr/bin/env python3
"""Mint a Metabase API key for the metabase MCP server and write
.devin/metabase-mcp.env — fully unattended.

Steps:
  1. Ensure the metabase container is up (docker compose up -d metabase,
     tolerating Hub rate-limits by retrying / waiting for health).
  2. Log in via /api/session with MB_EMAIL / MB_PASSWORD from the repo .env.
  3. POST /api/api-key {name: "devin-mcp", group_id: 2 (Administrators)}.
     If the key already exists (409/duplicate), delete it first and re-mint.
  4. Write METABASE_URL + METABASE_API_KEY to .devin/metabase-mcp.env.
  5. Smoke-test the wrapper (initialize handshake).

Usage: python mb_api_key_mint.py [--wait 300] [--url http://127.0.0.1:3000]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ENV_OUT = REPO / ".devin" / "metabase-mcp.env"
ADMIN_GROUP_ID = 2  # Metabase built-in Administrators group


def env_val(key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", (REPO / ".env").read_text(), re.M)
    return (m.group(1).strip().strip("\"'") if m else "")


def api(url: str, path: str, session: str | None = None, method: str = "GET", body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if session:
        headers["X-Metabase-Session"] = session
    req = urllib.request.Request(
        url + path, headers=headers, method=method,
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "body": e.read()[:300].decode(errors="replace")}


def metabase_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_container(url: str, wait: int) -> bool:
    if metabase_healthy(url):
        return True
    print("[mb] container not healthy — attempting up -d (Hub may rate-limit)")
    subprocess.run(["docker", "compose", "up", "-d", "metabase"], cwd=REPO,
                   capture_output=True, text=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if metabase_healthy(url):
            return True
        time.sleep(10)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=300)
    ap.add_argument("--url", default="http://127.0.0.1:3000")
    args = ap.parse_args()

    email, password = env_val("MB_EMAIL"), env_val("MB_PASSWORD")
    if not email or not password:
        print("MB_EMAIL/MB_PASSWORD missing or empty in .env — cannot mint key unattended")
        return 1

    if not ensure_container(args.url, args.wait):
        print(f"[mb] still unhealthy after {args.wait}s — retry later (Docker Hub pull may be rate-limited)")
        return 1
    print("[mb] healthy")

    login = api(args.url, "/api/session", method="POST", body={"username": email, "password": password})
    session = login.get("id")
    if not session:
        print(f"[mb] login failed: {login}")
        return 1

    # Delete any stale devin-mcp key so re-runs are idempotent
    keys = api(args.url, "/api/api-key", session=session)
    for k in keys if isinstance(keys, list) else []:
        if k.get("name") == "devin-mcp":
            api(args.url, f"/api/api-key/{k['id']}", session=session, method="DELETE")

    created = api(args.url, "/api/api-key", session=session,
                  method="POST", body={"name": "devin-mcp", "group_id": ADMIN_GROUP_ID})
    key = created.get("unmasked_key")
    if not key:
        print(f"[mb] key mint failed: {created}")
        return 1

    ENV_OUT.write_text(f"METABASE_URL={args.url}\nMETABASE_API_KEY={key}\n")
    print(f"[mb] wrote {ENV_OUT}")

    # Smoke-test the wrapper
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "mb_mint", "version": "1"}}}) + "\n"
    try:
        p = subprocess.Popen([r"C:\Program Files\Git\bin\bash.exe",
                              str(REPO / ".devin/skills/metabase-ops/scripts/metabase-mcp.sh")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        out, _ = p.communicate(init, timeout=20)
        print("[mb] wrapper handshake:", "OK" if '"result"' in out else f"FAIL {out[:80]}")
    except Exception as e:  # noqa: BLE001
        print(f"[mb] wrapper test skipped: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
