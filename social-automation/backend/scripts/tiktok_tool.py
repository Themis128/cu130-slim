#!/usr/bin/env python3
# ruff: noqa: E402
"""TikTok ops tooling — reads TikTok SocialAccounts + encrypted tokens from
the SocialAuto DB, then hits the official TikTok Open API (v2).

Complements app/api/tiktok.py (OAuth/publish surface) and the
tiktok-console-ops skill (dev-console/domain verification); this is the
read-mostly account/API ops surface for terminal use.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/tiktok_tool.py <cmd>

Commands:
    accounts        list TikTok SocialAccounts (id, handle, open_id, status)
    validate        validate token + return user info (open_id, display_name)
    creator         creator_info query — privacy options, max video duration,
                    comment/duet/stitch toggles (publish prerequisites)
    videos          recent videos with counts (Display API, video.list)
    status <id>     publish status for a publish_id (FILE_UPLOAD or
                    PULL_FROM_URL result tracking)
    dms             DM conversations (requires approved message scopes)

Options: -a/--account ACCOUNT_UUID (defaults to first tiktok account).
Never prints tokens.
"""
from __future__ import annotations

import argparse
import asyncio
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
            select(SocialAccount).where(SocialAccount.platform == "tiktok")
        )
        return list(result.scalars().all())


def _client_for(account):
    from app.core.security import decrypt_token
    from app.services.tiktok_api import TikTokAPIClient

    enc = account.access_token_enc
    token = decrypt_token(enc if isinstance(enc, bytes) else enc.encode())
    return TikTokAPIClient(access_token=token, open_id=account.account_id)


async def _pick(account_id: str | None):
    accounts = await _accounts()
    if not accounts:
        raise SystemExit("no tiktok accounts connected")
    if account_id:
        for a in accounts:
            if str(a.id).startswith(account_id):
                return a
        raise SystemExit(f"no tiktok account matching {account_id}")
    return accounts[0]


async def cmd_accounts() -> None:
    for a in await _accounts():
        print(
            f"{a.id}  @{a.username or '-'}  {a.display_name or ''}  "
            f"open_id={a.account_id[:12]}…  status={a.status}  "
            f"scopes={','.join(a.scopes or []) or '-'}"
        )


async def cmd_validate(account_id: str | None) -> None:
    account = await _pick(account_id)
    print(json.dumps(await _client_for(account).validate_token(), indent=2))


async def cmd_creator(account_id: str | None) -> None:
    account = await _pick(account_id)
    print(json.dumps(await _client_for(account).get_creator_info(), indent=2))


async def cmd_videos(account_id: str | None, limit: int) -> None:
    account = await _pick(account_id)
    data = await _client_for(account).list_videos(max_count=min(limit, 20))
    for v in data.get("data", {}).get("videos", []):
        print(
            f"{v.get('id')}  views={v.get('view_count', '-')} "
            f"likes={v.get('like_count', '-')} comments={v.get('comment_count', '-')}  "
            f"{(v.get('video_description') or v.get('title') or '')[:60]}"
        )


async def cmd_status(account_id: str | None, publish_id: str) -> None:
    account = await _pick(account_id)
    print(
        json.dumps(
            await _client_for(account).check_publish_status(publish_id), indent=2
        )
    )


async def cmd_dms(account_id: str | None) -> None:
    account = await _pick(account_id)
    print(
        json.dumps(
            await _client_for(account).list_dm_conversations(), indent=2
        )
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-a", "--account", default=None, help="account UUID (prefix ok)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts")
    sub.add_parser("validate")
    sub.add_parser("creator")
    p_vid = sub.add_parser("videos")
    p_vid.add_argument("--limit", type=int, default=20)
    p_st = sub.add_parser("status")
    p_st.add_argument("publish_id")
    sub.add_parser("dms")
    args = ap.parse_args()

    coro = {
        "accounts": lambda: cmd_accounts(),
        "validate": lambda: cmd_validate(args.account),
        "creator": lambda: cmd_creator(args.account),
        "videos": lambda: cmd_videos(args.account, args.limit),
        "status": lambda: cmd_status(args.account, args.publish_id),
        "dms": lambda: cmd_dms(args.account),
    }[args.cmd]()
    asyncio.run(coro)


if __name__ == "__main__":
    main()
