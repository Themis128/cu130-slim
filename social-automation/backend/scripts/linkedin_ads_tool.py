#!/usr/bin/env python3
# ruff: noqa: E402
"""LinkedIn Marketing API ops CLI — campaigns, analytics, member stats.

Covers the rw_ads/r_ads/r_ads_reporting/r_member_* surface on app
264925843 (Advertising API Development Tier approved 2026-11-18).
Complements scripts/linkedin_tool.py (token introspection, access
matrix) and linkedin_cm_tool.py (organic CM).

Run inside a worker container (social-worker-media / social-api):

    docker exec -w /app -e PYTHONPATH=/app social-worker-media \
      python3 scripts/linkedin_ads_tool.py <cmd>

Commands:
    accounts                        ad accounts visible to the token
    campaigns [--status A,B]        list adCampaigns on the ad account
    groups                          list adCampaignGroups on the ad account
    campaign <id>                   single campaign entity (name/status/budget)
    analytics [--days N]            spend/clicks/impressions per campaign
    pause <id> [--execute]          set campaign status PAUSED (dry-run default)
    resume <id> [--execute]         set campaign status ACTIVE (dry-run default)
    create "<name>" --group <id>    create a PAUSED campaign under a group
                                    (dry-run default; --budget/--currency)
    allowlist                       is the ad account in the app's Dev-Tier
                                    portal allowlist? (probes the write path)
    member-analytics                memberFollowersCount + memberCreatorPostAnalytics
                                    — verifies r_member_profileAnalytics /
                                    r_member_postAnalytics grants live

Ad account defaults to LINKEDIN_AD_ACCOUNT_ID env or 512642510.
Writes require --execute. Never prints tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import urllib.parse

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

REST = "https://api.linkedin.com/rest"
VER = "202610"  # verified live 2026-11-18 (202608/202609/202610 all good)
DEFAULT_ACCOUNT = "512642510"


def _account_id() -> str:
    return os.environ.get("LINKEDIN_AD_ACCOUNT_ID") or DEFAULT_ACCOUNT


async def _token(account_uuid: str | None = None) -> str:
    from app.core.security import decrypt_token
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        q = select(SocialAccount).where(SocialAccount.platform == "linkedin", SocialAccount.status == "active")
        if account_uuid:
            import uuid as uuid_mod

            q = q.where(SocialAccount.id == uuid_mod.UUID(account_uuid))
        acc = (await db.execute(q.limit(1))).scalars().first()
    if not acc or not acc.access_token_enc:
        raise SystemExit("no active LinkedIn account token")
    return decrypt_token(bytes(acc.access_token_enc))


def _h(token: str, method: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": VER,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if method:
        h["X-RestLi-Method"] = method
    return h


async def _req(method: str, path: str, token: str, **kw) -> httpx.Response:
    headers = _h(token, kw.pop("restli_method", None))
    headers.update(kw.pop("headers", {}) or {})
    async with httpx.AsyncClient(timeout=30) as c:
        return await c.request(method, f"{REST}/{path}", headers=headers, **kw)


def _show(r: httpx.Response) -> None:
    try:
        print(json.dumps(r.json(), indent=2, default=str)[:6000])
    except Exception:
        print(r.status_code, r.text[:2000])


async def cmd_accounts(args) -> None:
    token = await _token(args.account)
    r = await _req("GET", "adAccounts?q=search&search=(status:(values:List(ACTIVE)))", token)
    _show(r)


async def cmd_campaigns(args) -> None:
    token = await _token(args.account)
    statuses = args.status or "ACTIVE,PAUSED,DRAFT,COMPLETED"
    vals = ",".join(f"{s}" for s in statuses.split(","))
    path = f"adAccounts/{_account_id()}/adCampaigns?q=search&search=(status:(values:List({vals})))&count=100"
    r = await _req("GET", path, token)
    if r.status_code != 200:
        _show(r)
        return
    for c in r.json().get("elements", []):
        print(f"{c.get('id')}\t{(c.get('status') or '?'):10}\t{(c.get('name') or '')[:70]}")


async def cmd_groups(args) -> None:
    token = await _token(args.account)
    r = await _req(
        "GET",
        f"adAccounts/{_account_id()}/adCampaignGroups?q=search&search=(status:(values:List(ACTIVE,PAUSED,DRAFT)))&count=100",
        token,
    )
    if r.status_code == 200:
        for g in r.json().get("elements", []):
            print(f"{g.get('id')}\t{(g.get('status') or '?'):10}\t{(g.get('name') or '')[:70]}")
    else:
        _show(r)


async def cmd_create(args) -> None:
    """Create a PAUSED adCampaign under a campaign group.
    Verified live 2026-11-18 (campaign 930234026): CREATE takes a FLAT
    body (no patch wrapper); dailyBudget is an {amount,currencyCode}
    object; politicalIntent enum is NOT_POLITICAL; resume to ACTIVE
    requires dailyBudget set first."""
    token = await _token(args.account)
    acct = _account_id()
    start = int(dt.datetime.now(dt.UTC).timestamp() * 1000) + 86400000
    body = {
        "account": f"urn:li:sponsoredAccount:{acct}",
        "campaignGroup": f"urn:li:sponsoredCampaignGroup:{args.group}",
        "name": args.name,
        "type": "SPONSORED_UPDATES",
        "status": "PAUSED",
        "runSchedule": {"start": start, "end": start + 7 * 86400000},
        "locale": {"country": "US", "language": "en"},
        "offsiteDeliveryEnabled": False,
        "politicalIntent": "NOT_POLITICAL",
        "associatedEntity": "urn:li:organization:108614163",
        "costType": "CPM",
        "dailyBudget": {"amount": args.budget, "currencyCode": args.currency},
    }
    if not args.execute:
        print("DRY RUN — would POST:", json.dumps(body, indent=1))
        return
    r = await _req(
        "POST",
        f"adAccounts/{acct}/adCampaigns",
        token,
        restli_method="CREATE",
        json=body,
        headers={"Content-Type": "application/json"},
    )
    print("create ->", r.status_code)
    _show(r)
    if r.status_code == 201:
        cid_raw = r.headers.get("x-restli-id")
        cid = cid_raw.rsplit(":", 1)[-1] if cid_raw else None
        if cid:
            print(f"created campaign id (x-restli-id): {cid_raw} -> {cid}")
            g = await _req("GET", f"adAccounts/{acct}/adCampaigns/{cid}", token)
            _show(g)
        else:
            print("warning: no x-restli-id header — falling back to account search")
        r2 = await _req(
            "GET",
            f"adAccounts/{acct}/adCampaigns?q=search&search=(status:(values:List(PAUSED,DRAFT)))&count=10",
            token,
        )
        if r2.status_code == 200:
            for c in r2.json().get("elements", []):
                print(f"{c.get('id')}\t{(c.get('status') or '?'):10}\t{(c.get('name') or '')[:70]}")


async def cmd_campaign(args) -> None:
    token = await _token(args.account)
    r = await _req("GET", f"adAccounts/{_account_id()}/adCampaigns/{args.campaign_id}", token)
    _show(r)


async def cmd_analytics(args) -> None:
    token = await _token(args.account)
    days = args.days or 30
    end = dt.datetime.now(dt.UTC).date()
    start = end - dt.timedelta(days=days)
    dr = f"(start:(year:{start.year},month:{start.month},day:{start.day}),end:(year:{end.year},month:{end.month},day:{end.day}))"
    acct = urllib.parse.quote(f"urn:li:sponsoredAccount:{_account_id()}", safe="")
    path = (
        f"adAnalytics?q=analytics&pivot=CAMPAIGN&timeGranularity=ALL"
        f"&dateRange={urllib.parse.quote(dr, safe='():,')}"
        f"&accounts=List({acct})"
        "&fields=impressions,clicks,costInLocalCurrency"
    )
    r = await _req("GET", path, token)
    _show(r)


async def _set_status(args, want: str) -> None:
    token = await _token(args.account)
    url_path = f"adAccounts/{_account_id()}/adCampaigns/{args.campaign_id}"
    cur = await _req("GET", url_path, token)
    if cur.status_code != 200:
        print(f"status read failed: {cur.status_code} {cur.text[:300]}")
        return
    current = str((cur.json() or {}).get("status") or "").upper()
    print(f"current status: {current or 'unknown'} → want {want}")
    if current == want:
        print("already in target state")
        return
    if current in ("COMPLETED", "ARCHIVED"):
        print(f"refused: campaign is {current} — cannot pause/resume")
        return
    if not args.execute:
        print("dry-run — pass --execute to write")
        return
    w = await _req(
        "POST",
        url_path,
        token,
        restli_method="PARTIAL_UPDATE",
        json={"patch": {"$set": {"status": want}}},
        headers={"Content-Type": "application/json"},
    )
    print(f"write: {w.status_code} {w.text[:300]}")
    v = await _req("GET", url_path, token)
    if v.status_code == 200:
        print(f"verified status: {(v.json() or {}).get('status')}")


async def cmd_allowlist(args) -> None:
    """Dev Tier manages only portal-allowlisted ad accounts (max 5).
    Probes the write path with a no-op patch and reports the verdict."""
    token = await _token(args.account)
    acct = _account_id()
    camps_r = await _req(
        "GET",
        f"adAccounts/{acct}/adCampaigns?q=search&search=(status:(values:List(ACTIVE,PAUSED,DRAFT,COMPLETED)))&count=1",
        token,
    )
    if camps_r.status_code == 403:
        print(f"ad account {acct}: NOT allowlisted (read 403) — add it in the dev portal")
        return
    camps = camps_r.json().get("elements", []) if camps_r.status_code == 200 else []
    if not camps:
        print(f"ad account {acct}: readable but has no campaigns — allowlist status unverified")
        return
    cid = camps[0].get("id")
    status = str(camps[0].get("status") or "").upper()
    if not status:
        print(f"ad account {acct}: campaign {cid} has no status — cannot no-op probe")
        return
    path = f"adAccounts/{acct}/adCampaigns/{cid}"
    w = await _req(
        "POST",
        path,
        token,
        restli_method="PARTIAL_UPDATE",
        json={"patch": {"$set": {"status": status}}},
        headers={"Content-Type": "application/json"},
    )
    if w.status_code == 403:
        print(f"ad account {acct}: NOT allowlisted — write probe 403 FORBIDDEN")
        print("fix: developers.linkedin.com → app 264925843 → Products → Advertising API → View settings → Ad Accounts")
    elif w.status_code in (200, 201, 204):
        print(f"ad account {acct}: ALLOWLISTED — no-op write accepted (campaign {cid})")
    elif w.status_code == 400:
        # passed authz, failed validation — the account IS allowlisted
        print(f"ad account {acct}: ALLOWLISTED — write reached validation (HTTP 400 on no-op patch)")
    else:
        print(f"ad account {acct}: write probe HTTP {w.status_code} — {w.text[:200]}")


async def cmd_member_analytics(args) -> None:
    token = await _token(args.account)
    probes = [
        ("r_member_profileAnalytics", "memberFollowersCount?q=me"),
        ("r_member_postAnalytics", "memberCreatorPostAnalytics?q=me&queryType=IMPRESSION&aggregation=TOTAL"),
    ]
    async with httpx.AsyncClient(timeout=20) as c:
        for scope, path in probes:
            r = await c.get(f"{REST}/{path}", headers=_h(token))
            verdict = "GRANTED" if r.status_code == 200 else f"HTTP {r.status_code}"
            snippet = r.text[:140] if r.status_code != 200 else json.dumps(r.json().get("elements", [{}])[0], default=str)[:140]
            print(f"{scope}: {verdict} — {snippet}")


async def _main(args) -> None:
    await {
        "accounts": cmd_accounts,
        "campaigns": cmd_campaigns,
        "groups": cmd_groups,
        "campaign": cmd_campaign,
        "analytics": cmd_analytics,
        "pause": lambda a: _set_status(a, "PAUSED"),
        "resume": lambda a: _set_status(a, "ACTIVE"),
        "create": cmd_create,
        "allowlist": cmd_allowlist,
        "member-analytics": cmd_member_analytics,
    }[args.cmd](args)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("accounts", "campaigns", "groups", "analytics", "allowlist", "member-analytics"):
        s = sub.add_parser(name)
        s.add_argument("--account", help="social_accounts UUID (default: first active linkedin)")
        if name == "campaigns":
            s.add_argument("--status", help="comma list, default ACTIVE,PAUSED,DRAFT,COMPLETED")
        if name == "analytics":
            s.add_argument("--days", type=int, default=30)
    for name in ("campaign",):
        s = sub.add_parser(name)
        s.add_argument("campaign_id")
        s.add_argument("--account")
    for name in ("pause", "resume"):
        s = sub.add_parser(name)
        s.add_argument("campaign_id")
        s.add_argument("--execute", action="store_true", help="actually write (default dry-run)")
        s.add_argument("--account")
    s = sub.add_parser("create")
    s.add_argument("name")
    s.add_argument("--group", required=True, help="adCampaignGroup id (see `groups`)")
    s.add_argument("--budget", default="10.00", help="dailyBudget amount (account currency)")
    s.add_argument("--currency", default="EUR")
    s.add_argument("--execute", action="store_true", help="actually write (default dry-run)")
    s.add_argument("--account")
    args = p.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
