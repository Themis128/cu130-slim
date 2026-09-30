#!/usr/bin/env python3
"""Shared helpers for social-accounts-manager scripts."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, repo_root, social_api  # noqa: E402

IG_API = "http://localhost:8011"
LI_SIDECAR = "http://localhost:9225"
IG_SESSION_FILE = Path("/tmp/ig-sidecar-session.txt")
FB_PAGE_ACCOUNT_ID = "ca22c266-4a93-47bd-b3ed-32b38d0ffa7b"
LI_ORG = "cloudless-gr"


def http(method: str, url: str, headers: dict | None = None,
         form: dict | None = None, json_body: dict | None = None) -> str:
    h = dict(headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode()
    except urllib.error.URLError as e:
        return e.read().decode() if hasattr(e, "read") else str(e)


def api_py(code: str) -> str:
    """Run Python inside the social-api container."""
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", code],
        cwd=repo_root(), capture_output=True, text=True)
    out = "\n".join(l for l in r.stdout.splitlines()
                    if "INFO sqlalchemy" not in l)
    return out


def ig_sid() -> str:
    """Instagram sidecar session ID — env, session file, or login."""
    sid = env("IG_SESSION_ID")
    if not sid and IG_SESSION_FILE.exists():
        sid = IG_SESSION_FILE.read_text().strip()
    if sid:
        return sid
    print("→ No session found, logging in first...", file=sys.stderr)
    r = subprocess.run([sys.executable,
                        str(Path(__file__).resolve().parent / "ig-login.py")],
                       capture_output=True, text=True)
    sys.stderr.write(r.stderr)
    if IG_SESSION_FILE.exists():
        sid = IG_SESSION_FILE.read_text().strip()
    if not sid:
        print("  ERROR: Could not get Instagram session.", file=sys.stderr)
        sys.exit(1)
    return sid


def print_result(text: str) -> None:
    try:
        d = json.loads(text)
        print(f'  Result: {d.get("status", d.get("error", "unknown"))}')
    except ValueError:
        print(text[:200])
