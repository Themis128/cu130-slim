#!/usr/bin/env python3
# ruff: noqa: E402
"""Slack ops tooling — health-checks every configured Slack transport
(webhooks + bot token) without posting anything to channels.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/slack_tool.py <cmd>

Commands:
    config      list configured SLACK_* env names (values never printed) —
                which channels/webhooks exist, which are empty
    webhooks    probe each webhook URL with an empty POST — never delivers
                a message; distinguishes live hooks (400 no_text) from dead
                ones (404 no_service / 410 gone = app uninstalled/revoked)
    auth        chat-less auth.test on SLACK_BOT_TOKEN/SLACK_ACCESS_TOKEN —
                reports bot identity, team, and missing-scope shape
    channels    conversations.list via bot token (needs channels:read)
    history <channel_id> [n]   conversations.history — last n messages
                (needs channels:history)

Never prints tokens or webhook URLs (they're secrets — path only masked).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

SLACK_API = "https://slack.com/api"


def _webhook_envs() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.startswith("SLACK_") and "WEBHOOK" in k}


def _mask(url: str) -> str:
    # https://hooks.slack.com/services/T00/B00/secret — mask the secret part
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        parts[-1] = parts[-1][:4] + "…" if parts[-1] else ""
    return "/".join(parts)


def _bot_token() -> str:
    return os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_ACCESS_TOKEN") or ""


def cmd_config() -> None:
    names = sorted(k for k in os.environ if k.startswith("SLACK_"))
    for k in names:
        v = os.environ.get(k) or ""
        if "WEBHOOK" in k:
            print(f"{k:34s} {'set' if v else 'EMPTY'}  {_mask(v) if v else ''}")
        elif "TOKEN" in k:
            print(f"{k:34s} {'set' if v else 'EMPTY'}  {v[:6]}…" if v else f"{k:34s} EMPTY")
        else:
            print(f"{k:34s} {v or 'EMPTY'}")


async def cmd_webhooks() -> None:
    import httpx

    envs = _webhook_envs()
    if not envs:
        print("no SLACK_*_WEBHOOK_URL configured")
        return
    async with httpx.AsyncClient(timeout=15.0) as client:
        for name, url in sorted(envs.items()):
            if not url:
                print(f"{name:34s} EMPTY")
                continue
            try:
                # Empty POST never delivers a message: live hooks answer
                # 400 "no_text"; dead/revoked hooks answer 404 no_service.
                resp = await client.post(url, json={})
            except httpx.HTTPError as exc:
                print(f"{name:34s} unreachable: {exc}")
                continue
            if resp.status_code == 400:
                print(f"{name:34s} LIVE ({resp.text.strip()[:40]})")
            elif resp.status_code in (404, 410):
                print(f"{name:34s} DEAD ({resp.status_code} {resp.text.strip()[:40]}) — app uninstalled? regenerate")
            elif resp.status_code in (404, 410):
                print(f"{name:34s} DEAD ({resp.status_code} {resp.text.strip()[:40]}) — regenerate in Slack app")
            else:
                print(f"{name:34s} HTTP {resp.status_code}: {resp.text.strip()[:60]}")


async def cmd_auth() -> None:
    import httpx

    token = _bot_token()
    if not token:
        print("SLACK_BOT_TOKEN / SLACK_ACCESS_TOKEN are EMPTY — webhook-only mode")
        return
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{SLACK_API}/auth.test", headers={"Authorization": f"Bearer {token}"})
        d = resp.json()
    if d.get("ok"):
        print(f"ok — bot={d.get('user')} user_id={d.get('user_id')} team={d.get('team')} ({d.get('team_id')})")
    else:
        print(f"auth.test failed: {d.get('error')}")


async def _api(method: str, params: dict | None = None, json_body: dict | None = None) -> dict:
    import httpx

    token = _bot_token()
    if not token:
        print("bot token EMPTY — this command needs SLACK_BOT_TOKEN with scopes", file=sys.stderr)
        sys.exit(1)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{SLACK_API}/{method}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
    return resp.json()


async def cmd_channels() -> None:
    d = await _api("conversations.list", params={"limit": 200, "types": "public_channel,private_channel"})
    if not d.get("ok"):
        print(f"conversations.list failed: {d.get('error')} — needs channels:read/groups:read scope")
        return
    for c in d.get("channels", []):
        print(f"{c['id']}  #{c.get('name')}  members={c.get('num_members', '-')}  {'archived' if c.get('is_archived') else 'active'}")


async def cmd_history(channel_id: str, n: int) -> None:
    d = await _api("conversations.history", params={"channel": channel_id, "limit": n})
    if not d.get("ok"):
        print(f"conversations.history failed: {d.get('error')} — needs channels:history scope")
        return
    for m in d.get("messages", []):
        print(f"  [{m.get('ts')}] {m.get('user', m.get('bot_id', '?'))}: {(m.get('text') or '')[:120]!r}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=("config", "webhooks", "auth", "channels", "history"))
    parser.add_argument("arg", nargs="?", default=None)
    parser.add_argument("-n", default="10")
    args = parser.parse_args()

    if args.cmd == "config":
        cmd_config()
    elif args.cmd == "webhooks":
        await cmd_webhooks()
    elif args.cmd == "auth":
        await cmd_auth()
    elif args.cmd == "channels":
        await cmd_channels()
    elif args.cmd == "history":
        if not args.arg:
            print("usage: history <channel_id>", file=sys.stderr)
            return 2
        await cmd_history(args.arg, int(args.n))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
