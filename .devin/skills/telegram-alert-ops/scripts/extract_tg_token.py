#!/usr/bin/env python3
"""extract_tg_token.py — re-provision TELEGRAM_BOT_TOKEN into repo .env.

The canonical bot token lives Fernet-encrypted in Postgres
social_accounts (platform='telegram'); .env holds a copy for agents,
tg_alert.py and the Windows watchdog. If the token is ever rotated in
the DB, run this to refresh the .env copy.

    python3 extract_tg_token.py            # extract + write .env
    python3 extract_tg_token.py --check    # only compare, no write

Never prints the token. Runs the decrypt inside the social-api
container (has ENCRYPTION_KEY + DB access), so Docker must be up.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ENV = REPO_ROOT / ".env"

DECRYPT = r'''
import asyncio, asyncpg, os, base64 as b64
from cryptography.fernet import Fernet
async def m():
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    row = await conn.fetchrow("select meta_data->>\'bot_token_enc\' as me, access_token_enc as ae from social_accounts where platform=\'telegram\' limit 1")
    key = os.environ["ENCRYPTION_KEY"].encode()
    f = Fernet(b64.urlsafe_b64encode(key))
    for raw in (row["me"], row["ae"]):
        if not raw:
            continue
        blob = raw if isinstance(raw, (bytes, memoryview)) else raw.encode()
        try:
            print("TOK=" + f.decrypt(bytes(blob)).decode())
            return
        except Exception:
            continue
    print("TOK=")
asyncio.run(m())
'''


def _env_token() -> str:
    try:
        for ln in ENV.read_text().splitlines():
            if ln.startswith("TELEGRAM_BOT_TOKEN="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _db_token() -> str:
    r = subprocess.run(
        ["docker", "exec", "social-api", "python", "-c", DECRYPT],
        capture_output=True, text=True, timeout=60)
    for ln in r.stdout.splitlines():
        if ln.startswith("TOK="):
            return ln[4:].strip()
    raise SystemExit(f"extract failed: {r.stdout.strip() or r.stderr.strip()}")


def main() -> int:
    db = _db_token()
    if not db:
        print("no token decryptable in social_accounts — check account row")
        return 1
    cur = _env_token()
    if "--check" in sys.argv:
        print("match" if cur == db else "STALE — run without --check to update")
        return 0 if cur == db else 1
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    out, done = [], False
    for ln in lines:
        if ln.startswith("TELEGRAM_BOT_TOKEN="):
            out.append(f"TELEGRAM_BOT_TOKEN={db}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"TELEGRAM_BOT_TOKEN={db}")
    ENV.write_text("\n".join(out) + "\n")
    print(f".env updated (token len={len(db)}, changed={cur != db})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
