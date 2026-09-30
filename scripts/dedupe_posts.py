#!/usr/bin/env python3
"""Find and remove duplicate external publications (post_targets rows).

SAFETY: everything is a dry-run by default. An external delete only happens
with BOTH --delete and --yes on the `delete` subcommand.

Usage:
    python3 scripts/dedupe_posts.py list <post_id>
    python3 scripts/dedupe_posts.py delete <platform_post_id> --platform threads
    python3 scripts/dedupe_posts.py delete <platform_post_id> --platform threads --delete --yes

`list` shows every post_targets row for a post (joined to social_accounts)
and flags platform+account combos published more than once as DUPLICATE.

`delete` resolves the owning social_account inside social-api (same pattern
as app/services/publishing.py), then calls the platform client's delete:
    threads   -> ThreadsAPIClient.delete_post(media_id)
    facebook  -> FacebookAPIClient.delete_post(post_id)   (page token)
    linkedin  -> LinkedInAPIClient.delete_post(post_urn)
    twitter   -> TwitterAPIClient.delete_tweet(tweet_id)  (likely 402 —
                 credits depleted; falls back to "use the browser path")
    instagram/tiktok -> not supported via API, exit 2 (delete manually in app)

After a successful external delete, post_targets.status is set to 'deleted'
for matching rows via psql.

Exit codes: 0 ok / 1 error / 2 needs-manual-action.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSQL = [
    "docker", "compose", "exec", "-T", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-t", "-A", "-F", "\t",
]

ID_RE = re.compile(r"[a-zA-Z0-9_:.~\-]+")
PLATFORM_RE = re.compile(r"[a-z]+")
API_SUPPORTED = {"threads", "facebook", "linkedin", "twitter"}
NO_API = {"instagram", "tiktok"}


def sql(query: str) -> list[list[str]]:
    res = subprocess.run(
        [*PSQL, "-c", query], cwd=ROOT, capture_output=True, text=True
    )
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        sys.exit(1)
    return [r.split("\t") for r in res.stdout.strip().splitlines() if r.strip()]


def _check_id(value: str, what: str) -> str:
    if not ID_RE.fullmatch(value):
        print(f"ERROR: unsafe {what}: {value!r}")
        sys.exit(2)
    return value


# ── list ────────────────────────────────────────────────────────────────

def cmd_list(args) -> int:
    post_id = _check_id(args.post_id, "post_id")
    rows = sql(
        f"""SELECT t.social_account_id, sa.platform, coalesce(sa.username,''),
                   coalesce(t.platform_post_id,''), left(coalesce(t.platform_url,''),70),
                   t.status, coalesce(t.published_at::text,'')
            FROM post_targets t
            JOIN social_accounts sa ON sa.id = t.social_account_id
            WHERE t.post_id = '{post_id}'
            ORDER BY sa.platform, t.published_at NULLS LAST"""
    )
    if not rows:
        print(f"no post_targets rows for post {post_id}")
        return 0

    print(f"{'platform':<12}{'username':<24}{'status':<12}{'platform_post_id':<42}"
          f"{'published_at':<32}url")
    published_keys = Counter()
    for acct, platform, username, ppid, url, status, published in rows:
        print(f"{platform:<12}{username:<24}{status:<12}{ppid:<42}{published:<32}{url}")
        if status == "published":
            published_keys[(platform, acct)] += 1

    dupes = {k: n for k, n in published_keys.items() if n > 1}
    if dupes:
        print("\nDUPLICATES (same platform+account published more than once):")
        for (platform, acct), n in dupes.items():
            print(f"  - {platform} account {acct}: {n} published targets")
        return 2
    print("\nno duplicates found")
    return 0


# ── delete ──────────────────────────────────────────────────────────────

_DELETE_PREAMBLE = """
import asyncio, json
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.content import PostTarget
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token

PLATFORM = {platform!r}
PPID = {ppid!r}

async def _account(db):
    res = await db.execute(
        select(SocialAccount)
        .join(PostTarget, PostTarget.social_account_id == SocialAccount.id)
        .where(SocialAccount.platform == PLATFORM,
               PostTarget.platform_post_id == PPID)
    )
    acc = res.scalars().first()
    if acc is None:
        res = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == PLATFORM)
        )
        acc = res.scalars().first()
    return acc

async def main():
    async with async_session_maker() as db:
        acc = await _account(db)
        if acc is None:
            print("DELRESULT:" + json.dumps({{"ok": False, "error": "no social_account for platform " + PLATFORM}}))
            return
        try:
            token = decrypt_token(bytes(acc.access_token_enc))
        except Exception as e:
            print("DELRESULT:" + json.dumps({{"ok": False, "error": "token decrypt failed: " + str(e)[:300]}}))
            return
        try:
{body}
        except Exception as e:
            print("DELRESULT:" + json.dumps({{"ok": False, "error": str(e)[:400]}}))

asyncio.run(main())
"""

_DELETE_BODY = {
    "threads": """\
            from app.services.threads_api import ThreadsAPIClient
            client = ThreadsAPIClient(access_token=token, user_id=acc.account_id)
            ok = await client.delete_post(PPID)
            print("DELRESULT:" + json.dumps({"ok": bool(ok)}))
""",
    "facebook": """\
            from app.services.facebook_api import FacebookAPIClient
            from app.services.publishing import _facebook_page_token
            page_token = await _facebook_page_token(token, acc.account_id)
            client = FacebookAPIClient(access_token=page_token, page_id=acc.account_id)
            ok = await client.delete_post(PPID)
            print("DELRESULT:" + json.dumps({"ok": bool(ok)}))
""",
    "linkedin": """\
            from app.services.linkedin_api import LinkedInAPIClient
            client = LinkedInAPIClient(access_token=token)
            res = await client.delete_post(PPID)
            print("DELRESULT:" + json.dumps({"ok": bool(res.success), "error": getattr(res, "error", None)}))
""",
    "twitter": """\
            from app.services.twitter_api import TwitterAPIClient
            client = TwitterAPIClient(access_token=token)
            ok = await client.delete_tweet(PPID)
            print("DELRESULT:" + json.dumps({"ok": bool(ok), "note": "False = tweet already gone (404)"}))
""",
}


def _find_targets(ppid: str, platform: str) -> list[list[str]]:
    return sql(
        f"""SELECT t.post_id, t.social_account_id, coalesce(t.platform_url,''),
                   t.status, coalesce(t.published_at::text,'')
            FROM post_targets t
            JOIN social_accounts sa ON sa.id = t.social_account_id
            WHERE t.platform_post_id = '{ppid}' AND sa.platform = '{platform}'"""
    )


def cmd_delete(args) -> int:
    ppid = _check_id(args.platform_post_id, "platform_post_id")
    platform = (args.platform or "").lower()
    if not PLATFORM_RE.fullmatch(platform):
        print("ERROR: --platform is required (threads|facebook|linkedin|twitter|instagram|tiktok)")
        return 2

    if platform in NO_API:
        print(f"{platform}: not supported via API — delete the post manually in the app")
        return 2
    if platform not in API_SUPPORTED:
        print(f"unknown platform {platform!r}; supported: {sorted(API_SUPPORTED)} "
              f"or manual-only: {sorted(NO_API)}")
        return 2

    targets = _find_targets(ppid, platform)
    print(f"external delete: platform={platform} platform_post_id={ppid}")
    if targets:
        print("matching post_targets rows:")
        for post_id, acct, url, status, published in targets:
            print(f"  post_id={post_id} account={acct} status={status} "
                  f"published_at={published} url={url}")
    else:
        print("  (no post_targets rows reference this platform_post_id — external delete only)")

    if platform == "twitter":
        print("\nNOTE: twitter API deletes currently fail with 402 (credits depleted).")
        print("      Prefer the browser path (browser-novnc session for twitter) to delete.")

    if not args.delete:
        print("\nDRY-RUN — re-run with --delete --yes to actually delete externally.")
        return 0
    if not args.yes:
        print("\nERROR: --delete requires --yes (explicit confirmation). Nothing deleted.")
        return 2

    print(f"\nDeleting on {platform} via social-api ...")
    snippet = _DELETE_PREAMBLE.format(
        platform=platform, ppid=ppid, body=_DELETE_BODY[platform]
    )
    res = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python", "-c", snippet],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    if res.returncode != 0:
        print(res.stderr or res.stdout, file=sys.stderr)
        return 1

    result = None
    for line in res.stdout.splitlines():
        if line.startswith("DELRESULT:"):
            result = json.loads(line[len("DELRESULT:"):])
    if result is None:
        print(f"ERROR: could not parse delete result:\n{res.stdout}\n{res.stderr}")
        return 1

    if not result.get("ok"):
        print(f"external delete FAILED: {result.get('error') or result}")
        if platform == "twitter":
            print("twitter: expected 402 (credits depleted) — delete via the browser instead:")
            print("  POST http://localhost:9223/session/start {\"platform\": \"twitter\"}")
            return 2
        return 1

    print(f"external delete OK: {ppid}")
    if targets:
        sql(
            f"""UPDATE post_targets SET status='deleted'
                WHERE platform_post_id = '{ppid}'
                  AND social_account_id IN
                      (SELECT id FROM social_accounts WHERE platform = '{platform}')"""
        )
        print(f"post_targets marked 'deleted' ({len(targets)} row(s))")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("post_id")
    p.set_defaults(f=cmd_list)

    p = sub.add_parser("delete")
    p.add_argument("platform_post_id")
    p.add_argument("--platform", required=True)
    p.add_argument("--delete", action="store_true", help="perform the external delete")
    p.add_argument("--yes", action="store_true", help="required together with --delete")
    p.set_defaults(f=cmd_delete)

    args = ap.parse_args()
    return args.f(args)


if __name__ == "__main__":
    sys.exit(main())
