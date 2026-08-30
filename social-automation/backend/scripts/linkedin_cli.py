#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""CLI for LinkedIn setup, AI content generation, and manual API testing.

Runs inside the social-api container (or anywhere with DATABASE_URL set) and
uses the same modules the API endpoints do. It never prints decrypted tokens.

Examples (inside the social-api container):
    python scripts/linkedin_cli.py --list
    python scripts/linkedin_cli.py --validate --account-id 4a8d9440-47d2-4bda-bd11-3776fd9022ba
    python scripts/linkedin_cli.py --generate-post "Why serverless matters" --tone professional
    python scripts/linkedin_cli.py --publish --account-id <id> --commentary "Hello world"
    python scripts/linkedin_cli.py --followers --account-id <id>
    python scripts/linkedin_cli.py --analytics --account-id <id> --post-urn urn:li:share:123
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import Any

# Allow running as /app/scripts/linkedin_cli.py inside the container, or from
# the repo root on a host with the backend path injected.
_CONTAINER = "/app"
_REPO_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "social-automation", "backend")
for path in (_CONTAINER, _REPO_BACKEND):
    if path not in sys.path and os.path.isdir(path):
        sys.path.insert(0, path)

from sqlalchemy import select

from app.core.security import decrypt_token
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.linkedin_api import LinkedInAPIClient
from app.services.linkedin_ai import (
    generate_linkedin_article,
    generate_linkedin_hashtags,
    generate_linkedin_post,
    improve_linkedin_post,
    suggest_best_time_to_post,
)


def _print_json(label: str, data: Any) -> None:
    import json

    print(f"\n{label}:")
    print(json.dumps(data, indent=2, default=str))


async def _admin_user_and_team(db):
    email = os.environ.get("SOCIAL_ADMIN_EMAIL")
    if not email:
        raise SystemExit("Set SOCIAL_ADMIN_EMAIL in the environment")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        raise SystemExit(f"Admin user not found: {email}")
    team = (
        await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user.id))
    ).scalars().first()
    return user, team


async def _load_account(db, account_id: uuid.UUID) -> SocialAccount:
    account = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.id == account_id,
                SocialAccount.platform == "linkedin",
            )
        )
    ).scalar_one_or_none()
    if not account:
        raise SystemExit(f"LinkedIn account not found: {account_id}")
    return account


async def cmd_list(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        _, team = await _admin_user_and_team(db)
        if not team:
            raise SystemExit("No team for admin user")
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team.id,
                SocialAccount.platform == "linkedin",
            )
        )
        accounts = result.scalars().all()
        if not accounts:
            print("No LinkedIn accounts connected.")
            return
        for a in accounts:
            meta = a.meta_data or {}
            print(
                f"{a.id}\t{a.display_name or a.username or a.account_id}\t"
                f"type={meta.get('account_type', 'person')}\tstatus={a.status}"
            )


async def cmd_validate(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        account = await _load_account(db, args.account_id)
        token = decrypt_token(account.access_token_enc)
        client = LinkedInAPIClient(access_token=token)
        profile = await client.validate_token()
        orgs = await client.get_member_organizations()
        _print_json("Profile", profile)
        _print_json(
            "Organizations",
            [{"urn": o.urn, "name": o.name, "vanity_name": o.vanity_name, "role": o.role} for o in orgs],
        )


async def cmd_generate_post(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        _, team = await _admin_user_and_team(db)
        result = await generate_linkedin_post(
            args.topic,
            tone=args.tone,
            length=args.length,
            include_hashtags=not args.no_hashtags,
            include_site_link=not args.no_site_link,
            site=args.site,
            provider=args.provider,
            model=args.model,
            db=db,
            team_id=team.id,
        )
        _print_json("Generated post", result)


async def cmd_generate_article(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        _, team = await _admin_user_and_team(db)
        result = await generate_linkedin_article(
            args.topic,
            tone=args.tone,
            sections=args.sections,
            include_takeaways=not args.no_takeaways,
            include_cta=not args.no_cta,
            provider=args.provider,
            model=args.model,
            db=db,
            team_id=team.id,
        )
        _print_json("Generated article", result)


async def cmd_improve_post(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        _, team = await _admin_user_and_team(db)
        result = await improve_linkedin_post(
            args.content,
            goal=args.goal,
            tone=args.tone,
            provider=args.provider,
            model=args.model,
            db=db,
            team_id=team.id,
        )
        _print_json("Improved post", result)


async def cmd_hashtags(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        _, team = await _admin_user_and_team(db)
        hashtags = await generate_linkedin_hashtags(
            args.content,
            count=args.count,
            provider=args.provider,
            model=args.model,
            db=db,
            team_id=team.id,
        )
        _print_json("Hashtags", {"hashtags": hashtags})


async def cmd_best_time(args: argparse.Namespace) -> None:
    times = await suggest_best_time_to_post(account_type="organization")
    _print_json("Best times", {"best_times": times})


async def cmd_publish(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        account = await _load_account(db, args.account_id)
        token = decrypt_token(account.access_token_enc)
        client = LinkedInAPIClient(access_token=token)
        meta = account.meta_data or {}
        account_type = (meta.get("account_type") or "person").lower()
        if account_type in ("organization", "company", "page"):
            author_urn = f"urn:li:organization:{account.account_id}"
        elif meta.get("author_urn"):
            author_urn = str(meta["author_urn"])
        else:
            author_urn = f"urn:li:person:{account.account_id}"
        result = await client.create_post(
            author_urn=author_urn,
            commentary=args.commentary,
            link_url=args.link_url,
            link_title=args.link_title,
            link_description=args.link_description,
            visibility=args.visibility,
        )
        _print_json("Publish result", {
            "success": result.success,
            "platform_post_id": result.platform_post_id,
            "platform_url": result.platform_url,
            "error": result.error,
        })


async def cmd_comment(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        account = await _load_account(db, args.account_id)
        token = decrypt_token(account.access_token_enc)
        client = LinkedInAPIClient(access_token=token)
        meta = account.meta_data or {}
        account_type = (meta.get("account_type") or "person").lower()
        if account_type in ("organization", "company", "page"):
            creator_urn = f"urn:li:organization:{account.account_id}"
        elif meta.get("author_urn"):
            creator_urn = str(meta["author_urn"])
        else:
            creator_urn = f"urn:li:person:{account.account_id}"
        result = await client.create_comment(
            post_urn=args.post_urn,
            text=args.text,
            creator_urn=creator_urn,
        )
        _print_json("Comment result", {
            "success": result.success,
            "platform_post_id": result.platform_post_id,
            "platform_url": result.platform_url,
            "error": result.error,
        })


async def cmd_followers(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        account = await _load_account(db, args.account_id)
        token = decrypt_token(account.access_token_enc)
        client = LinkedInAPIClient(access_token=token)
        meta = account.meta_data or {}
        if meta.get("author_urn") and str(meta["author_urn"]).startswith("urn:li:organization:"):
            org_urn = str(meta["author_urn"])
        else:
            org_urn = f"urn:li:organization:{account.account_id}"
        count = await client.get_follower_count(org_urn)
        _print_json("Followers", {"org_urn": org_urn, "followers": count})


async def cmd_analytics(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        account = await _load_account(db, args.account_id)
        token = decrypt_token(account.access_token_enc)
        client = LinkedInAPIClient(access_token=token)
        meta = account.meta_data or {}
        if meta.get("author_urn") and str(meta["author_urn"]).startswith("urn:li:organization:"):
            org_urn = str(meta["author_urn"])
        else:
            org_urn = f"urn:li:organization:{account.account_id}"
        stats = await client.get_post_analytics(args.post_urn, org_urn)
        _print_json("Post analytics", stats)


async def cmd_org_analytics(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        account = await _load_account(db, args.account_id)
        token = decrypt_token(account.access_token_enc)
        client = LinkedInAPIClient(access_token=token)
        meta = account.meta_data or {}
        if meta.get("author_urn") and str(meta["author_urn"]).startswith("urn:li:organization:"):
            org_urn = str(meta["author_urn"])
        else:
            org_urn = f"urn:li:organization:{account.account_id}"
        stats = await client.get_organization_lifetime_stats(org_urn)
        _print_json("Organization analytics", stats)


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid UUID: {value}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LinkedIn AI + API test CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List connected LinkedIn accounts")

    p = sub.add_parser("validate", help="Validate a LinkedIn token and list Company Pages")
    p.add_argument("--account-id", type=_parse_uuid, required=True)

    p = sub.add_parser("generate-post", help="Generate a LinkedIn post with AI")
    p.add_argument("topic", help="Post topic")
    p.add_argument("--tone", default="professional")
    p.add_argument("--length", default="medium", choices=("short", "medium", "long"))
    p.add_argument("--no-hashtags", action="store_true")
    p.add_argument("--no-site-link", action="store_true")
    p.add_argument("--site", default="www.cloudless.gr")
    p.add_argument("--provider", default="cloudflare")
    p.add_argument("--model", default=None)

    p = sub.add_parser("generate-article", help="Generate a long-form LinkedIn article")
    p.add_argument("topic", help="Article topic")
    p.add_argument("--tone", default="professional")
    p.add_argument("--sections", type=int, default=5)
    p.add_argument("--no-takeaways", action="store_true")
    p.add_argument("--no-cta", action="store_true")
    p.add_argument("--provider", default="cloudflare")
    p.add_argument("--model", default=None)

    p = sub.add_parser("improve-post", help="Improve an existing LinkedIn post")
    p.add_argument("--content", required=True)
    p.add_argument("--goal", default="engagement")
    p.add_argument("--tone", default="professional")
    p.add_argument("--provider", default="cloudflare")
    p.add_argument("--model", default=None)

    p = sub.add_parser("hashtags", help="Suggest hashtags for content")
    p.add_argument("--content", required=True)
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--provider", default="cloudflare")
    p.add_argument("--model", default=None)

    sub.add_parser("best-time", help="Show LinkedIn best-time-to-post windows")

    p = sub.add_parser("publish", help="Publish a text or link post")
    p.add_argument("--account-id", type=_parse_uuid, required=True)
    p.add_argument("--commentary", required=True)
    p.add_argument("--link-url", default=None)
    p.add_argument("--link-title", default=None)
    p.add_argument("--link-description", default=None)
    p.add_argument("--visibility", default="PUBLIC")

    p = sub.add_parser("comment", help="Post a comment")
    p.add_argument("--account-id", type=_parse_uuid, required=True)
    p.add_argument("--post-urn", required=True)
    p.add_argument("--text", required=True)

    p = sub.add_parser("followers", help="Get Company Page follower count")
    p.add_argument("--account-id", type=_parse_uuid, required=True)

    p = sub.add_parser("analytics", help="Get analytics for a single post")
    p.add_argument("--account-id", type=_parse_uuid, required=True)
    p.add_argument("--post-urn", required=True)

    p = sub.add_parser("org-analytics", help="Get lifetime analytics for the Company Page")
    p.add_argument("--account-id", type=_parse_uuid, required=True)

    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "validate": cmd_validate,
        "generate-post": cmd_generate_post,
        "generate-article": cmd_generate_article,
        "improve-post": cmd_improve_post,
        "hashtags": cmd_hashtags,
        "best-time": cmd_best_time,
        "publish": cmd_publish,
        "comment": cmd_comment,
        "followers": cmd_followers,
        "analytics": cmd_analytics,
        "org-analytics": cmd_org_analytics,
    }

    fn = dispatch[args.command]
    if asyncio.iscoroutinefunction(fn):
        await fn(args)
    else:
        fn()


if __name__ == "__main__":
    asyncio.run(main())
