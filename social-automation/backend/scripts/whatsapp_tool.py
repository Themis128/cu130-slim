#!/usr/bin/env python3
# ruff: noqa: E402
"""WhatsApp Business ops tooling via facebook_business SDK WABA objects.

Complements app/services/whatsapp_api.py (which owns send/verify/register —
message sends stay there; this tool is read-only ops surface).

Run inside the social-api container (env vars are already set there):

    docker exec social-api python3 /app/scripts/whatsapp_tool.py <cmd>

Commands:
    status        phone number info: display name, quality rating,
                  verification + name status, messaging limits
    templates     message templates on the WABA with status/category/language
    waba          WABA overview: id, name, currency, timezone, health
    webhook       app subscription state on the WABA

Env used: WHATSAPP_ACCESS_TOKEN, WHATSAPP_BUSINESS_ACCOUNT_ID,
WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_ID. Never prints tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)


def _need(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise SystemExit(f"{name} not set")
    return val


def _init() -> None:
    from facebook_business.api import FacebookAdsApi

    FacebookAdsApi.init(access_token=_need("WHATSAPP_ACCESS_TOKEN"), crash_log=False)


def _graph_get(path: str, **params) -> dict:
    """Direct Graph GET — some WA edges lack SDK objects or the SDK edge
    maps to a capability-gated path while the direct call works."""
    import requests

    resp = requests.get(
        f"https://graph.facebook.com/v21.0/{path.lstrip('/')}",
        params=params,
        headers={"Authorization": f"Bearer {_need('WHATSAPP_ACCESS_TOKEN')}"},
        timeout=15,
    )
    data = resp.json()
    if "error" in data:
        raise SystemExit(json.dumps(data["error"], indent=2))
    return data


def cmd_status() -> None:
    info = _graph_get(
        _need("WHATSAPP_PHONE_NUMBER_ID"),
        fields="display_phone_number,verified_name,quality_rating,"
        "code_verification_status,name_status,messaging_limit_tier,"
        "platform_type,is_official_business_account",
    )
    print(json.dumps(info, indent=2))


def cmd_templates() -> None:
    data = _graph_get(
        f"{_need('WHATSAPP_BUSINESS_ACCOUNT_ID')}/message_templates",
        fields="name,status,category,language,rejected_reason",
        limit=50,
    )
    rows = data.get("data", [])
    if not rows:
        print("(no message templates on the WABA)")
    for t in rows:
        rej = f" rejected={t['rejected_reason']}" if t.get("rejected_reason") else ""
        print(
            f"{t.get('name')}\t{t.get('status')}\t{t.get('category')}\t"
            f"{t.get('language')}{rej}"
        )


def cmd_waba() -> None:
    _init()
    from facebook_business.adobjects.whatsappbusinessaccount import (
        WhatsAppBusinessAccount,
    )

    waba = WhatsAppBusinessAccount(fbid=_need("WHATSAPP_BUSINESS_ACCOUNT_ID"))
    info = waba.api_get(
        fields=[
            "name",
            "currency",
            "timezone_id",
            "message_template_namespace",
            "account_review_status",
            "business_verification_status",
            "health_status",
        ]
    )
    print(json.dumps(dict(info), indent=2, default=str))


def cmd_webhook() -> None:
    _init()
    from facebook_business.adobjects.whatsappbusinessaccount import (
        WhatsAppBusinessAccount,
    )

    waba = WhatsAppBusinessAccount(fbid=_need("WHATSAPP_BUSINESS_ACCOUNT_ID"))
    subs = waba.get_subscribed_apps()
    data = [dict(s) for s in subs] if subs else []
    print(json.dumps(data, indent=2, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "templates", "waba", "webhook"):
        sub.add_parser(name)
    args = ap.parse_args()

    {
        "status": cmd_status,
        "templates": cmd_templates,
        "waba": cmd_waba,
        "webhook": cmd_webhook,
    }[args.cmd]()


if __name__ == "__main__":
    main()
