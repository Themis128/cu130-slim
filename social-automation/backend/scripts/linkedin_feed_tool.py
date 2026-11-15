#!/usr/bin/env python3
"""linkedin_feed_tool.py — ops CLI for the LinkedIn Community Management feed.

Run inside the backend container (or with the backend venv):

    python scripts/linkedin_feed_tool.py posts --author urn:li:organization:12345
    python scripts/linkedin_feed_tool.py metadata --urns urn:li:share:1,urn:li:share:2
    python scripts/linkedin_feed_tool.py comments --urn urn:li:share:1
    python scripts/linkedin_feed_tool.py react --actor member --root urn:li:share:1 --type LIKE
    python scripts/linkedin_feed_tool.py comment --actor member --object urn:li:share:1 --text "…"
    python scripts/linkedin_feed_tool.py engage          # run the watchlist task now
    python scripts/linkedin_feed_tool.py rules           # list conversion rules
    python scripts/linkedin_feed_tool.py ensure-rules    # ensure conversion rules

Writes honor LINKEDIN_FEED_WRITES (default off → dry_run payloads).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


async def main() -> int:
    ap = argparse.ArgumentParser(description="LinkedIn feed/conversion ops")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("posts")
    p.add_argument("--author", required=True)
    p.add_argument("--count", type=int, default=10)

    p = sub.add_parser("metadata")
    p.add_argument("--urns", required=True, help="comma-separated urn:li: URNs")

    p = sub.add_parser("comments")
    p.add_argument("--urn", required=True)
    p.add_argument("--count", type=int, default=10)

    p = sub.add_parser("react")
    p.add_argument("--actor", choices=["member", "organization"], default="member")
    p.add_argument("--root", required=True)
    p.add_argument("--type", default="LIKE")

    p = sub.add_parser("comment")
    p.add_argument("--actor", choices=["member", "organization"], default="member")
    p.add_argument("--object", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--parent")

    sub.add_parser("engage")
    sub.add_parser("rules")
    sub.add_parser("ensure-rules")

    p = sub.add_parser("lead-notifications")
    p.add_argument("--owner", required=True, help="urn:li:sponsoredAccount:… or urn:li:organization:…")
    p.add_argument("--type", default="SPONSORED", choices=["SPONSORED", "EVENT", "COMPANY", "ORGANIZATION_PRODUCT"])

    p = sub.add_parser("register-lead-sync")
    p.add_argument("--webhook", required=True, help="public HTTPS webhook URL")
    p.add_argument("--owner", required=True)
    p.add_argument("--type", default="SPONSORED", choices=["SPONSORED", "EVENT", "COMPANY", "ORGANIZATION_PRODUCT"])

    sub.add_parser("community-moderate")
    sub.add_parser("offline-conversions")
    sub.add_parser("ads-guard")

    p = sub.add_parser("admins")
    p.add_argument("--action", choices=["list", "set", "remove"], default="list")
    p.add_argument("--person", help="urn:li:person:… or member id")
    p.add_argument("--role", default="ADMINISTRATOR")
    p.add_argument("--state", default="REQUESTED", choices=["REQUESTED", "APPROVED"])

    p = sub.add_parser("events")
    p.add_argument("--action", choices=["list", "create", "post"], default="list")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--start-ms", type=int)
    p.add_argument("--end-ms", type=int)
    p.add_argument("--event-url")
    p.add_argument("--event-id")
    p.add_argument("--commentary", default="")

    args = ap.parse_args()

    from app.services import linkedin_advanced, linkedin_feed

    if args.cmd == "posts":
        _print(await linkedin_feed.list_posts(args.author, args.count))
    elif args.cmd == "metadata":
        _print(await linkedin_feed.social_metadata(args.urns.split(",")))
    elif args.cmd == "comments":
        _print(await linkedin_feed.list_comments(args.urn, args.count))
    elif args.cmd == "react":
        _print(await linkedin_feed.react(args.actor, args.root, args.type))
    elif args.cmd == "comment":
        _print(await linkedin_feed.comment(args.actor, args.object, args.text, args.parent))
    elif args.cmd == "engage":
        from app.worker.tasks.linkedin_engagement import _run

        _print(await _run())
    elif args.cmd == "rules":
        _print(await linkedin_advanced.list_conversion_rules())
    elif args.cmd == "ensure-rules":
        from app.worker.tasks.linkedin_conversion_ensure import _ensure

        _print(await _ensure())
    elif args.cmd == "lead-notifications":
        _print(await linkedin_advanced.list_lead_notifications(args.owner, args.type))
    elif args.cmd == "register-lead-sync":
        _print(await linkedin_advanced.create_lead_notification(args.webhook, args.owner, args.type))
    elif args.cmd == "community-moderate":
        from app.worker.tasks.linkedin_community import _run as _community_run

        _print(await _community_run())
    elif args.cmd == "offline-conversions":
        from app.worker.tasks.linkedin_offline_conversions import _run as _oc_run

        _print(await _oc_run())
    elif args.cmd == "ads-guard":
        from app.worker.tasks.linkedin_ads_guard import _run as _guard_run

        _print(await _guard_run())
    elif args.cmd == "admins":
        from app.services import linkedin_admin

        if args.action == "list":
            _print(await linkedin_admin.list_acls())
        elif args.action == "set":
            _print(await linkedin_admin.set_role(args.person, args.role, args.state))
        else:
            _print(await linkedin_admin.remove_role(args.person, args.role))
    elif args.cmd == "events":
        from app.services import linkedin_events

        if args.action == "list":
            _print(await linkedin_events.list_org_events())
        elif args.action == "create":
            _print(await linkedin_events.create_org_event(args.name, args.start_ms, args.end_ms, description=args.description or "", event_url=args.event_url))
        else:
            _print(await linkedin_events.post_org_event(args.event_id, commentary=args.commentary))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
