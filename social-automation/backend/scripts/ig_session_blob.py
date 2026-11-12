#!/usr/bin/env python3
"""ig_session_blob.py — capture/restore the full instagrapi session blob.

The private-API DM lane (InstagramPrivateDMClient) can auth with a bare
``sessionid`` cookie, but instagrapi's own docs are explicit: a cold
sessionid login on a fresh device fingerprint is what triggers IG
checkpoints. ``Client.dump_settings()`` serialises the device fingerprint,
cookies, headers and user pk — loading it makes the next login look like
the same "phone" resuming, dropping the challenge rate by ~an order of
magnitude.

This tool captures that blob from a live sessionid and stores it
encrypted in ``social_accounts.meta_data.private_api_session_settings``
(next to ``private_api_session_id``), which the DM worker decrypts and
hands to the client.

Runs INSIDE the social-api container (instagrapi is --no-deps installed
there; it is intentionally absent from pyproject). DB access uses asyncpg.

Usage:
    python scripts/ig_session_blob.py --account <uuid> --sessionid <sid> [--proxy URL]
    python scripts/ig_session_blob.py --account <uuid> --verify
    python scripts/ig_session_blob.py --account <uuid> --clear

--verify loads the stored blob into a fresh Client and reports whether
the session is still alive (user_id resolves). --clear removes the blob
key, falling the lane back to bare sessionid auth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _connect():
    import asyncpg

    dsn = os.environ.get("DATABASE_URL", "")
    return await asyncpg.connect(dsn)


async def _get_meta(conn, account_id: str) -> dict:
    row = await conn.fetchrow(
        "SELECT meta_data FROM social_accounts WHERE id = $1::uuid", account_id
    )
    if not row:
        raise SystemExit(f"account {account_id} not found")
    meta = row["meta_data"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return meta or {}


async def _save_meta(conn, account_id: str, meta: dict) -> None:
    await conn.execute(
        "UPDATE social_accounts SET meta_data = $1::jsonb WHERE id = $2::uuid",
        json.dumps(meta),
        account_id,
    )


def _blob_client(session_id: str, proxy: str | None, blob_path: str | None = None):
    from instagrapi import Client

    cl = Client()
    if proxy:
        try:
            cl.set_proxy(proxy)
        except Exception:
            print(f"WARN: could not set proxy {proxy}", file=sys.stderr)
    cl.set_locale("el_GR")
    cl.set_timezone_offset(10800)
    if blob_path:
        cl.load_settings(blob_path)
    cl.login_by_sessionid(session_id)
    return cl


def cmd_capture(args) -> int:
    from app.core.security import decrypt_field, encrypt_field

    conn = asyncio.run(_connect())
    meta = asyncio.run(_get_meta(conn, args.account))
    sid_enc = meta.get("private_api_session_id")
    session_id = args.sessionid or (decrypt_field(sid_enc) if sid_enc else None)
    if not session_id:
        raise SystemExit("no sessionid: pass --sessionid or set private_api_session_id")

    import tempfile

    cl = _blob_client(session_id, args.proxy)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        path = fh.name
    try:
        cl.dump_settings(path)
        blob = open(path).read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    meta["private_api_session_settings"] = encrypt_field(blob)
    # keep the plain sessionid fresh too — the blob augments, not replaces
    if not sid_enc:
        meta["private_api_session_id"] = encrypt_field(session_id)
    asyncio.run(_save_meta(conn, args.account, meta))
    print(f"OK: stored settings blob ({len(blob)} bytes) for account {args.account}")
    print(f"    device={cl.device_settings.get('device_name', '?')} user_id={cl.user_id}")
    return 0


def cmd_verify(args) -> int:
    from app.core.security import decrypt_field

    conn = asyncio.run(_connect())
    meta = asyncio.run(_get_meta(conn, args.account))
    sid = decrypt_field(meta.get("private_api_session_id"))
    blob_enc = meta.get("private_api_session_settings")
    if not sid or not blob_enc:
        print("no stored blob/sessionid — run --sessionid capture first")
        return 1

    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(decrypt_field(blob_enc))
        path = fh.name
    try:
        cl = _blob_client(sid, args.proxy, blob_path=path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    print(f"OK: session alive — user_id={cl.user_id} handle={getattr(cl, 'user', None) and cl.user.username}")
    return 0


def cmd_clear(args) -> int:
    conn = asyncio.run(_connect())
    meta = asyncio.run(_get_meta(conn, args.account))
    if meta.pop("private_api_session_settings", None) is None:
        print("no blob stored — nothing to clear")
        return 0
    asyncio.run(_save_meta(conn, args.account, meta))
    print(f"OK: cleared private_api_session_settings for {args.account}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", required=True, help="social_accounts.id (uuid)")
    p.add_argument("--sessionid", help="sessionid cookie value (capture only)")
    p.add_argument("--proxy", default=os.environ.get("INSTAGRAM_PROXY") or None)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true")
    g.add_argument("--clear", action="store_true")
    g.add_argument("--capture", action="store_true")
    args = p.parse_args()
    if args.capture:
        return cmd_capture(args)
    if args.verify:
        return cmd_verify(args)
    return cmd_clear(args)


if __name__ == "__main__":
    sys.exit(main())
