#!/usr/bin/env python3
"""Authenticated SocialAuto API caller for ops/debug work.

Handles the 2FA admin login end-to-end so one-off endpoint checks don't
need hand-rolled login scripts:

    docker exec -i social-api python3 /app/scripts/ops_api.py GET /api/v1/accounts
    docker exec -i social-api python3 /app/scripts/ops_api.py POST \
        "/api/v1/ops/daily-digest?days=1&post_to_slack=true&post_to_email=false"
    docker exec -i social-api python3 /app/scripts/ops_api.py POST /api/v1/ops/session-heal --raw

Auth chain:
  1. SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD from env (set in the container).
  2. TOTP: SOCIAL_TOTP_SECRET env if present, else the admin user's
     two_factor_secret read from Postgres (the api container does not get
     SOCIAL_TOTP_SECRET in its env — n8n does).
  3. POST /api/v1/auth/login (form) -> bearer token -> request.

Base URL defaults to http://localhost:8000 (in-container); override with
--base or OPS_API_BASE (e.g. http://localhost:8083 from the host — but then
SOCIAL_TOTP_SECRET must come from .env since there is no DB session).

Never prints secrets. --summary prints status + a trimmed JSON body;
--raw prints the full JSON response.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.parse
import urllib.request

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)


def _totp(secret: str) -> str:
    s = secret.strip().replace(" ", "").upper().rstrip("=")
    s += "=" * (-len(s) % 8)
    key = base64.b32decode(s)
    msg = struct.pack(">Q", int(time.time() // 30))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off : off + 4])[0] & 0x7FFFFFFF) % 1000000
    return str(code).zfill(6)


async def _totp_secret_from_db(email: str) -> str | None:
    try:
        from sqlalchemy import select

        from app.db.session import async_session_maker
        from app.models.user import User
    except Exception:
        return None
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        return user.two_factor_secret if user else None


def _request(method: str, url: str, *, data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _trim(obj, limit: int = 600):
    """Return a compact, depth-trimmed representation for --summary."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            s = json.dumps(v, default=str)
            out[k] = v if len(s) <= limit else s[:limit] + "…"
        return out
    if isinstance(obj, list):
        return [_trim(x, limit) for x in obj[:10]] + ([f"… +{len(obj) - 10} more"] if len(obj) > 10 else [])
    return obj


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("method", choices=["GET", "POST", "PUT", "DELETE", "PATCH"])
    ap.add_argument("path", help="API path, e.g. /api/v1/ops/daily-digest?days=1&post_to_slack=true")
    ap.add_argument("--body", help="JSON body for POST/PUT/PATCH")
    ap.add_argument("--base", default=os.environ.get("OPS_API_BASE", "http://localhost:8000"))
    ap.add_argument("--raw", action="store_true", help="print full JSON response")
    args = ap.parse_args()

    email = os.environ.get("SOCIAL_ADMIN_EMAIL", "").strip()
    password = os.environ.get("SOCIAL_ADMIN_PASSWORD", "")
    if not email or not password:
        print("error: SOCIAL_ADMIN_EMAIL/SOCIAL_ADMIN_PASSWORD not in env", file=sys.stderr)
        return 2

    secret = os.environ.get("SOCIAL_TOTP_SECRET", "").strip() or (await _totp_secret_from_db(email))
    form = {"username": email, "password": password}
    if secret:
        form["otp"] = _totp(secret)

    login = _request(
        "POST",
        args.base + "/api/v1/auth/login",
        data=urllib.parse.urlencode(form).encode(),
        timeout=30,
    )
    token = login.get("access_token")
    if not token:
        print("error: login returned no access_token", file=sys.stderr)
        return 2

    body = args.body.encode() if args.body else (b"" if args.method in ("POST", "PUT", "PATCH") else None)
    headers = {"Authorization": f"Bearer {token}"}
    if args.body:
        headers["Content-Type"] = "application/json"
    try:
        resp = _request(
            args.method,
            args.base + args.path,
            data=body,
            headers=headers,
        )
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read()[:400].decode(errors='replace')}", file=sys.stderr)
        return 1

    print(json.dumps(resp if args.raw else _trim(resp), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
