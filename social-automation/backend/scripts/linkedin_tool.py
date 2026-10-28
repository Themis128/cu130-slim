#!/usr/bin/env python3
# ruff: noqa: E402
"""LinkedIn ops tooling via the official linkedin-api-client (Rest.li).

Complements scripts/linkedin_cli.py (which covers content generation,
publishing, followers, analytics). This covers the gaps: real token
introspection, per-account endpoint access matrix, and ad-account discovery.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/linkedin_tool.py <cmd>

Commands:
    accounts                          list LinkedIn SocialAccount rows
    token-inspect --account <uuid>    OAuth2 introspection: active, scopes,
                                      expiry, client_id (needs LINKEDIN_CLIENT_*)
    access-matrix --account <uuid>    probe the known endpoint variants and
                                      report status per endpoint (the live
                                      truth table behind follower-analytics)
    ad-accounts --account <uuid>      ad accounts visible to the token
                                      (Marketing API; dev tier may 403)

Never prints tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid as uuid_mod

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

import urllib.parse

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

API_BASE = "https://api.linkedin.com"
API_VERSION = "202510"


async def _get_account(account_id: str):
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(SocialAccount).where(SocialAccount.id == uuid_mod.UUID(account_id))
            )
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"no such social account: {account_id}")
        db.expunge(row)
        return row


def _token(account) -> str:
    from app.core.security import decrypt_token

    return decrypt_token(bytes(account.access_token_enc))


def _org_urn(account) -> str:
    meta = account.meta_data or {}
    urn = meta.get("author_urn") or ""
    if str(urn).startswith("urn:li:organization:"):
        return str(urn)
    return f"urn:li:organization:{account.account_id}"


def cmd_accounts() -> None:
    async def _run():
        from app.db.session import async_session_maker
        from app.models.social_account import SocialAccount

        async with async_session_maker() as db:
            rows = (
                await db.execute(
                    select(SocialAccount).where(SocialAccount.platform == "linkedin")
                )
            ).scalars().all()
            for r in rows:
                meta = r.meta_data or {}
                print(
                    f"{r.id}\t{r.username or r.account_id}\t"
                    f"type={meta.get('account_type', '?')}\t"
                    f"expires={r.token_expires_at}\tstatus={r.status}"
                )

    asyncio.run(_run())


def cmd_token_inspect(account_id: str) -> None:
    """Real OAuth2 introspection — active flag, scopes, expiry, client."""
    from app.core.config import settings

    if not (settings.LINKEDIN_CLIENT_ID and settings.LINKEDIN_CLIENT_SECRET):
        raise SystemExit("LINKEDIN_CLIENT_ID/SECRET not configured")
    account = asyncio.run(_get_account(account_id))
    from linkedin_api.clients.auth.client import AuthClient

    auth = AuthClient(
        client_id=settings.LINKEDIN_CLIENT_ID,
        client_secret=settings.LINKEDIN_CLIENT_SECRET,
        redirect_url="",
    )
    resp = auth.introspect_access_token(_token(account))
    keep = {
        k: getattr(resp, k, None)
        for k in (
            "active",
            "status",
            "scope",
            "expires_at",
            "created_at",
            "authorized_at",
            "client_id",
            "auth_type",
        )
    }
    keep = {k: v for k, v in keep.items() if v is not None}
    keep["account"] = account.username or account.account_id
    print(json.dumps(keep, indent=2))


def cmd_access_matrix(account_id: str) -> None:
    """Probe every LinkedIn endpoint variant we depend on and report status.

    Live truth table for the follower-analytics guard: which endpoints this
    token+account combo can actually call.
    """
    account = asyncio.run(_get_account(account_id))
    token = _token(account)
    org_urn = _org_urn(account)
    org_id = org_urn.rsplit(":", 1)[-1]

    probes = {
        "userinfo": ("GET", f"{API_BASE}/v2/userinfo", {}),
        "networkSizes_firstDegree": (
            "GET",
            f"{API_BASE}/v2/networkSizes/{urllib.parse.quote(org_urn, safe='')}",
            {"edgeType": "CompanyFollowedByMember"},
        ),
        "followerStatistics": (
            "GET",
            f"{API_BASE}/rest/organizationalEntityFollowerStatistics",
            {
                "q": "organizationalEntity",
                "organizationalEntity": org_urn,
            },
        ),
        "organizationAcl_check": (
            "GET",
            f"{API_BASE}/rest/organizationAcls",
            {"q": "roleAssignee"},
        ),
        "organization": (
            "GET",
            f"{API_BASE}/rest/organizations/{org_id}",
            {},
        ),
        "socialMetadata_likes": (
            "GET",
            f"{API_BASE}/rest/socialMetadata/likes",
            {},
        ),
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": API_VERSION,
    }
    with httpx.Client(timeout=20) as client:
        for name, (method, url, params) in probes.items():
            try:
                resp = client.request(method, url, params=params, headers=headers)
                body_hint = ""
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                        if "elements" in body:
                            body_hint = f"elements={len(body['elements'])}"
                        elif "firstDegreeSize" in body:
                            body_hint = f"firstDegreeSize={body['firstDegreeSize']}"
                    except Exception:
                        pass
                else:
                    body_hint = resp.text[:120].replace("\n", " ")
                print(f"{name}\t{resp.status_code}\t{body_hint}")
            except Exception as exc:  # noqa: BLE001
                print(f"{name}\tERR\t{exc}")


def cmd_ad_accounts(account_id: str) -> None:
    account = asyncio.run(_get_account(account_id))
    token = _token(account)
    url = (
        f"{API_BASE}/rest/adAccounts?q=search"
        "&search=(status:(values:List(ACTIVE,DRAFT)))"
    )
    with httpx.Client(timeout=20) as client:
        resp = client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "LinkedIn-Version": API_VERSION,
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return
    for el in resp.json().get("elements", []):
        print(f"{el.get('id')}\t{el.get('name')}\t{el.get('status')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts")
    for name in ("token-inspect", "access-matrix", "ad-accounts"):
        p = sub.add_parser(name)
        p.add_argument("--account", required=True)
    args = ap.parse_args()

    {
        "accounts": cmd_accounts,
        "token-inspect": lambda: cmd_token_inspect(args.account),
        "access-matrix": lambda: cmd_access_matrix(args.account),
        "ad-accounts": lambda: cmd_ad_accounts(args.account),
    }[args.cmd]()


if __name__ == "__main__":
    main()
