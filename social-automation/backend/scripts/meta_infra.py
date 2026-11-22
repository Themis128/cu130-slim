#!/usr/bin/env python3
"""meta_infra.py — Meta ad-infra provisioning via Business Management API.

Idempotent chain the Nov-24 retry used to drive in the browser:
  probe   -> system_users list, BM visibility, WABA status
  chain   -> system user -> app install -> non-expiring token ->
             WABA assign -> new ad account -> appeals pass
Each step skips itself when already done; all writes logged to
/app/uploads/meta-infra-log.jsonl. Runs INSIDE social-api.

Requires a USER token with business_management + ads_management (the
whatsapp OAuth row). Personal-account restriction gates mutations with
OAuthException subcode 2859015 ("temporarily blocked") — re-run after
id/hub identity clears.
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

GRAPH = "https://graph.facebook.com/v21.0"
BM = os.environ.get("META_BM_ID", "1558125105019725")
APP = os.environ.get("META_APP_ID", "1936126137016578")
WABA = os.environ.get("META_WABA_ID", "1717901383017723")
SYSUSER_NAME = os.environ.get("META_SYSUSER_NAME", "cloudless-bot")
SCOPES = (
    "whatsapp_business_management,whatsapp_business_messaging,"
    "business_management,ads_management,ads_read,"
    "instagram_basic,instagram_content_publish,pages_show_list,"
    "pages_read_engagement,pages_manage_posts"
)
LOG = Path("/app/uploads/meta-infra-log.jsonl")


def g(path, token, params=None, post=None):
    if post is not None:
        d = dict(post)
        d["access_token"] = token
        req = urllib.request.Request(f"{GRAPH}/{path}", data=urllib.parse.urlencode(d, doseq=True).encode())
    else:
        url = f"{GRAPH}/{path}?access_token={urllib.parse.quote(token)}"
        if params:
            url += "&" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        try:
            return {"_err": e.code, **json.loads(e.read() or b"{}")}
        except Exception:
            return {"_err": e.code}
    except Exception as e:
        return {"_err": str(e)[:150]}


def restricted(resp):
    """True when the failure is a known human-gate (silent-wait state).

    Covers the personal restriction (subcode 2859015) and the app-capability
    gate (#100 'apps with the capability') — both clear only via the id/hub
    identity action or an app-claim, not by retrying, so the beat maps them
    to rc3 instead of alerting daily."""
    err = resp.get("error", {})
    msg = str(err.get("error_user_msg", "")).lower() + str(err.get("message", "")).lower()
    return err.get("error_subcode") == 2859015 or "restricted" in msg or "with the capability" in msg


def log(step, result):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(UTC).isoformat(), "step": step, "result": result}) + "\n")


async def _token():
    import sys as _s

    _s.path.insert(0, "/app/scripts")
    import meta_appeals as m

    return await m._fb_token()


def probe(tok):
    print("== probe ==")
    su = g(f"{BM}/system_users", tok, params={"limit": "50"})
    users = su.get("data", [])
    print("system_users:", json.dumps(users)[:300])
    waba = g(WABA, tok, params={"fields": "id,name,account_review_status"})
    print("waba:", json.dumps(waba)[:300])
    accts = g(f"{BM}/owned_ad_accounts", tok, params={"fields": "account_id,name,account_status"})
    print("owned_ad_accounts:", json.dumps(accts)[:400])
    return users


def chain(tok):
    print("== chain ==")
    # 1) system user
    sus = g(f"{BM}/system_users", tok).get("data", [])
    su = next((u for u in sus if u.get("name") == SYSUSER_NAME), None)
    if su:
        print("1) system user exists:", su.get("id"))
    else:
        r = g(f"{BM}/system_users", tok, post={"name": SYSUSER_NAME, "role": "ADMIN"})
        print("1) create system user:", json.dumps(r)[:300])
        log("system_users.create", r)
        if r.get("id"):
            su = r
        elif restricted(r):
            print("   -> restricted (id/hub gate); rerun after identity clears")
            return 3
        else:
            return 1
    suid = su.get("id") if su else None
    if not suid:
        print("1) no system user id — abort")
        return 3
    # 2) install app on system user
    r = g(f"{suid}/applications", tok, post={"business_app": APP})
    print("2) app install:", json.dumps(r)[:250])
    log("applications.install", r)
    if "error" in r:
        return 3 if restricted(r) else 1
    # 3) generate non-expiring system-user token
    r = g(f"{BM}/access_token", tok, post={"business_app": APP, "scope": SCOPES, "set_token_expires_in_60_days": "false", "system_user_id": suid})
    print("3) access_token:", "MINTED" if r.get("access_token") else json.dumps(r)[:300])
    log("access_token.generate", {k: v for k, v in r.items() if k != "access_token"})
    if "error" in r:
        return 3 if restricted(r) else 1
    su_tok = r.get("access_token", tok)
    # 4) assign WABA to system user
    r = g(f"{WABA}/assigned_users", su_tok, post={"user": suid, "tasks": '["MANAGE"]'})
    print("4) waba assign:", json.dumps(r)[:250])
    log("waba.assign", r)
    if "error" in r:
        return 3 if restricted(r) else 1
    # 5) ad account — reuse a live one; create only when none exists
    existing = g(f"{BM}/owned_ad_accounts", tok, params={"fields": "account_id,name,account_status"}).get("data", [])
    live = next((a for a in existing if a.get("account_status") in (1, "1")), None)
    if live:
        print("5) ad account exists:", live.get("account_id"))
        r = {"account_id": live["account_id"], "reused": True}
    else:
        r = g(
            f"{BM}/adaccount",
            tok,
            post={"name": "cloudless.gr-ads", "currency": "EUR", "timezone_id": "105", "end_advertiser": "NONE", "media_agency": "NONE", "partner": "NONE"},
        )
        print("5) ad account:", json.dumps(r)[:350])
    log("adaccount.resolve", r)
    if restricted(r):
        print("   -> restricted; rerun after identity clears")
        return 3
    if "error" in r:
        return 1
    # 6) appeals pass on any live entities
    sys.path.insert(0, "/app/scripts")
    import meta_appeals as m

    new_id = r.get("account_id")
    if new_id:
        m._appeal(su_tok, "accounts", [new_id])
    return 0


async def _all_tokens():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import decrypt_token
    from app.models.social_account import SocialAccount

    e = create_async_engine(os.environ["DATABASE_URL"])
    S = async_sessionmaker(e)
    async with S() as db:
        rows = (
            (
                await db.execute(
                    select(SocialAccount).where(SocialAccount.platform.in_(["facebook", "instagram", "threads", "whatsapp"]), SocialAccount.status == "active")
                )
            )
            .scalars()
            .all()
        )
    out = {}
    for a in rows:
        try:
            out[f"{a.platform}/{a.username}"] = decrypt_token(a.access_token_enc)
        except Exception:
            pass
    return out


def audit():
    """Full Meta-infra audit: debug_token every active row + live IG probe."""
    import asyncio as aio

    ts = aio.run(_all_tokens())
    for label, tok in ts.items():
        d = g("debug_token", tok, params={"input_token": tok}).get("data", {})
        print(f"{label}: type={d.get('type')} valid={d.get('is_valid')} expires={d.get('expires_at')} app={d.get('app_id')}")
    ig = ts.get("instagram/cloudless.gr")
    if ig:
        try:
            r = urllib.request.urlopen(f"https://graph.instagram.com/v26.0/me?fields=id,username&access_token={ig}", timeout=20)
            print("IG graph.instagram.com/me:", r.read()[:200])
        except Exception as e:
            print("IG probe FAIL:", str(e)[:150])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "audit":
        audit()
        return 0
    tok = asyncio.run(_token())
    if cmd == "probe":
        probe(tok)
        return 0
    if cmd == "chain":
        return chain(tok)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
