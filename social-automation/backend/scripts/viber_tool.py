#!/usr/bin/env python3
# ruff: noqa: E402
"""Viber Bot ops tooling — reads bot accounts + encrypted tokens from the
SocialAuto DB, then hits the official Viber REST API.

Complements app/api/viber.py (which owns the HTTP surface); this tool is a
read-mostly ops surface for terminal use.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/viber_tool.py <cmd>

Commands:
    accounts      list Viber SocialAccounts (id, name, uri, subscribers)
    info          live get_account_info for an account
    webhook       webhook url + subscribed event types
    online        check online status for a comma-separated user-id list
    user          get_user_details for one viber user id
    verify-sig    self-check: sign a probe body and verify it (crypto sanity)

Options: -a/--account ACCOUNT_UUID (defaults to first viber account).
Never prints tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)


async def _accounts() -> list:
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == "viber")
        )
        return list(result.scalars().all())


def _token_for(account) -> str:
    from app.core.security import decrypt_token

    meta = account.meta_data or {}
    raw = meta.get("viber_auth_token_enc") or ""
    if raw:
        return decrypt_token(raw.encode() if isinstance(raw, str) else raw)
    if account.access_token_enc:
        enc = account.access_token_enc
        return decrypt_token(enc if isinstance(enc, bytes) else enc.encode())
    raise SystemExit("no viber auth token stored for this account")


def _client_for(account):
    from app.services.viber_api import ViberAPIClient

    return ViberAPIClient(_token_for(account))


async def _pick(account_id: str | None):
    accounts = await _accounts()
    if not accounts:
        raise SystemExit("no viber accounts connected (POST /api/v1/viber/connect)")
    if account_id:
        for a in accounts:
            if str(a.id).startswith(account_id):
                return a
        raise SystemExit(f"no viber account matching {account_id}")
    return accounts[0]


async def cmd_accounts() -> None:
    for a in await _accounts():
        meta = a.meta_data or {}
        print(
            f"{a.id}  {a.display_name or a.username or a.account_id}  "
            f"uri={meta.get('bot_uri', '-')}  subs={meta.get('subscribers_count', '-')}  "
            f"status={a.status}  webhook={'set' if meta.get('webhook_set') else 'unset'}"
        )


async def cmd_info(account_id: str | None) -> None:
    account = await _pick(account_id)
    client = _client_for(account)
    data = await client.get_account_info()
    print(json.dumps(data, indent=2))


async def cmd_webhook(account_id: str | None) -> None:
    account = await _pick(account_id)
    client = _client_for(account)
    data = await client.get_account_info()
    print(
        json.dumps(
            {
                "webhook": data.get("webhook"),
                "event_types": data.get("event_types"),
                "expected_url": f"…/api/v1/viber/webhook/{account.id}",
            },
            indent=2,
        )
    )


async def cmd_online(account_id: str | None, user_ids: str) -> None:
    account = await _pick(account_id)
    client = _client_for(account)
    ids = [u.strip() for u in user_ids.split(",") if u.strip()]
    print(json.dumps(await client.get_online(ids), indent=2))


async def cmd_user(account_id: str | None, user_id: str) -> None:
    account = await _pick(account_id)
    client = _client_for(account)
    print(json.dumps(await client.get_user_details(user_id), indent=2))


async def cmd_verify_sig(account_id: str | None) -> None:
    account = await _pick(account_id)
    token = _token_for(account)
    body = b'{"event":"webhook"}'
    sig = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    from app.services.viber_api import verify_signature

    ok = verify_signature(body, sig, token)
    bad = verify_signature(body, sig, token + "x")
    print(json.dumps({"valid_sig_accepted": ok, "wrong_token_rejected": not bad}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-a", "--account", default=None, help="account UUID (prefix ok)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts")
    sub.add_parser("info")
    sub.add_parser("webhook")
    p_online = sub.add_parser("online")
    p_online.add_argument("user_ids", help="comma-separated viber user ids")
    p_user = sub.add_parser("user")
    p_user.add_argument("user_id")
    sub.add_parser("verify-sig")
    args = ap.parse_args()

    coro = {
        "accounts": lambda: cmd_accounts(),
        "info": lambda: cmd_info(args.account),
        "webhook": lambda: cmd_webhook(args.account),
        "online": lambda: cmd_online(args.account, args.user_ids),
        "user": lambda: cmd_user(args.account, args.user_id),
        "verify-sig": lambda: cmd_verify_sig(args.account),
    }[args.cmd]()
    asyncio.run(coro)


if __name__ == "__main__":
    main()
