#!/usr/bin/env python3
"""meta_appeals.py — official Meta Appeals API driver.

Files numbered appeals (appeal_case_id) for restricted ad accounts and
Business Managers through the documented Business Management API —
no browser wizard required. Runs INSIDE the social-api container.

Commands:
  status                        token scopes, BMs, per-account restriction map
  appeal-accounts <id> [id...]  POST <bm>/ad_review_requests
  appeal-bm <id> [id...]        POST <bm>/bm_review_requests

Every run appends a JSONL transcript to
/app/uploads/meta-appeals-log.jsonl — never prints the token.
"""

import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

GRAPH = "https://graph.facebook.com/v21.0"
LOG = Path("/app/uploads/meta-appeals-log.jsonl")


def _g(path, token, params=None, method=None, data=None):
    url = f"{GRAPH}/{path}?access_token={urllib.parse.quote(token)}"
    if params:
        url += "&" + urllib.parse.urlencode(params, doseq=True)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode()
        url = f"{GRAPH}/{path}"
        data = dict(data)
        data["access_token"] = token
        body = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(url, data=body, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        try:
            return {"_http_error": e.code, **json.loads(e.read() or b"{}")}
        except Exception:
            return {"_http_error": e.code}


def _log(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now(UTC).isoformat()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


async def _fb_token():
    """First active token whose debug_token reports type USER.

    The Appeals API rejects Page tokens; WhatsApp/facebook OAuth rows hold
    the user-grant token (business_management rides the whatsapp flow).
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import decrypt_token
    from app.models.social_account import SocialAccount

    engine = create_async_engine(os.environ["DATABASE_URL"])
    S = async_sessionmaker(engine)
    async with S() as db:
        rows = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.platform.in_(["facebook", "whatsapp"]),
                    SocialAccount.status == "active",
                )
            )
        ).scalars().all()
    candidates = []
    for a in rows:
        try:
            candidates.append((a.platform, a.username, decrypt_token(a.access_token_enc)))
        except Exception:
            continue
    for platform, username, token in candidates:
        t = _g("debug_token", token, params={"input_token": token}).get("data", {}).get("type")
        if t == "USER":
            print(f"token: USER via {platform}/{username}")
            return token
    if candidates:
        platform, username, token = candidates[0]
        print(f"WARN: no USER token; using {platform}/{username} (appeals may 400)")
        return token
    raise SystemExit("no active facebook/whatsapp account row")


def _status(token):
    dbg = _g("debug_token", token, params={"input_token": token})
    data = dbg.get("data", {})
    print("token type:", data.get("type"), "| app:", data.get("app_id"))
    print("scopes:", ",".join(data.get("scopes", [])))
    bms = _g("me/businesses", token, params={"fields": "id,name"})
    for b in bms.get("data", []):
        print("BM:", b.get("name"), b.get("id"))
        accts = _g(f"{b['id']}/owned_ad_accounts", token,
                   params={"fields": "account_id,name,account_status"})
        for a in accts.get("data", []):
            print("  ad acct:", a.get("name"), a.get("account_id"),
                  "status:", a.get("account_status"))
    return data


def _appeal(token, kind, ids):
    bms = _g("me/businesses", token, params={"fields": "id,name"}).get("data", [])
    if not bms:
        print("no Business Managers visible to this token")
        _log({"action": kind, "ids": ids, "result": "no-bms"})
        return 2
    dbg = _g("debug_token", token, params={"input_token": token}).get("data", {})
    app_id = dbg.get("app_id", "")
    for bm in bms:
        ep = "ad_review_requests" if kind == "accounts" else "bm_review_requests"
        key = "ad_account_ids" if kind == "accounts" else "business_manager_ids"
        resp = _g(f"{bm['id']}/{ep}", token, method="POST",
                  data={key: json.dumps(ids), "app": app_id})
        print(f"BM {bm['name']} ({bm['id']}):", json.dumps(resp, indent=1)[:1200])
        _log({"action": kind, "bm": bm["id"], "ids": ids,
              "app": app_id, "result": resp})
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    token = asyncio.run(_fb_token())
    if cmd == "status":
        _status(token)
    elif cmd == "appeal-accounts" and len(sys.argv) > 2:
        return _appeal(token, "accounts", sys.argv[2:])
    elif cmd == "appeal-bm" and len(sys.argv) > 2:
        return _appeal(token, "bm", sys.argv[2:])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
