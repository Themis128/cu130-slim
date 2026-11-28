#!/usr/bin/env python3
"""wa_meta_token.py — resolve a LIVE Meta Business-Manager token.

The .env META_ACCESS_TOKEN is a user token that dies on any Meta password /
session invalidation (OAuthException 190/460). The whatsapp row in
social_accounts holds a never-expiring system-user token with
business_management + whatsapp_business_management — exactly what the BM
edges (owned_apps / client_apps / system_users) need.

resolve_token() prefers the live DB token (cached ~1h in a chmod-600 state
file) and falls back to META_ACCESS_TOKEN from ~/cu130-slim/.env.
Token material is never logged or printed by callers.
"""

import os
import subprocess
import time

REPO = os.path.expanduser("~/cu130-slim")
STATE = os.path.expanduser("~/.local/state/wa-meta-token/token.txt")
TTL = 3600

# Runs INSIDE social-api: decrypt the newest whatsapp access_token_enc.
_DECRYPT = """
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token

async def m():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as c:
        r = await c.execute(text(
            "SELECT access_token_enc FROM social_accounts "
            "WHERE platform='whatsapp' ORDER BY updated_at DESC LIMIT 1"))
        row = r.fetchone()
    print("TOK:" + (decrypt_token(row[0]) if row else ""))

asyncio.run(m())
"""


def _env_token() -> str:
    try:
        with open(os.path.join(REPO, ".env")) as fh:
            for line in fh:
                if line.startswith("META_ACCESS_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _cached() -> str:
    try:
        if time.time() - os.path.getmtime(STATE) < TTL:
            with open(STATE) as fh:
                return fh.read().strip()
    except OSError:
        pass
    return ""


def _live_token() -> str:
    cached = _cached()
    if cached:
        return cached
    try:
        out = subprocess.run(
            ["docker", "exec", "social-api", "/opt/venv/bin/python", "-c", _DECRYPT],
            capture_output=True, text=True, timeout=40,
        )
        tok = ""
        for line in out.stdout.splitlines():
            if line.startswith("TOK:"):
                tok = line[4:].strip()
        if not tok and out.stdout.strip():
            tok = out.stdout.strip().splitlines()[-1].strip()
    except Exception:
        tok = ""
    if tok:
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(tok)
        except OSError:
            pass
    return tok


def resolve_token() -> str:
    """Live DB token first (never expires), .env fallback."""
    return _live_token() or _env_token()


if __name__ == "__main__":
    t = resolve_token()
    print("token-resolved" if t else "no-token")
