#!/usr/bin/env python3
# ruff: noqa: E402
"""LinkedIn newsletter subscriber nurture lane.

Reaches out to members of the EspoCRM TargetList "LinkedIn newsletter"
(subscribers of The Cloudless Build Log) through the LinkedIn browser
sidecar. Two lanes, chosen from the `Inbox action` field captured at ingest:

    Message  — 1st-degree: open the profile composer and DM them
    Connect  — 2nd/3rd-degree: send a connection invite with a short note
    Follow   — follow-only profiles: skipped (no invite surface)

Compliance posture: this is FIRST-TOUCH outreach to people who already
subscribed to the newsletter — friendly, one clear question, no pitch, no
links. Paced slowly (jittered sleeps), hard-capped per run, and every touch
writes a CRM note + flips the Lead status New -> In Process so re-runs are
idempotent.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/newsletter_nurture.py list
    docker exec social-api python3 /app/scripts/newsletter_nurture.py run --dry-run --limit 3
    docker exec social-api python3 /app/scripts/newsletter_nurture.py run --approve --limit 5

Commands:
    list                 cohort breakdown by inbox action + status
    run                  process --limit leads (default --dry-run; --approve
                         actually sends; --action message|connect|all)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

from app.services.espocrm_client import (  # noqa: E402
    add_lead_note,
    espo_get,
    espo_patch,
)
from app.services.linkedin_sidecar import (  # noqa: E402
    LinkedInSidecarClient,
    LinkedInSidecarError,
)

TARGET_LIST_NAME = "LinkedIn newsletter"
# Hard daily ceilings — well under LinkedIn's ~100 invites/wk invite limit
# and sane human pacing for first-touch DMs.
MAX_PER_RUN = 20
MESSAGE_PAUSE = (45, 120)  # seconds between touches, jittered

MESSAGE_TEMPLATE = (
    "Hi {first} — thanks for subscribing to The Cloudless Build Log. "
    "Quick question while I plan the next editions: is there a cloud or "
    "self-hosting topic you'd like me to cover? If not, no worries at all — "
    "just glad you're reading."
)

CONNECT_NOTE_TEMPLATE = (
    "Hi {first} — thanks for subscribing to The Cloudless Build Log! "
    "Would be great to connect — I share weekly build-in-public notes on "
    "cloud + self-hosted infra here."
)


def _profile_url(desc: str) -> str | None:
    m = re.search(r"Profile:\s*(https?://\S+)", desc or "")
    return m.group(1) if m else None


def _inbox_action(desc: str) -> str:
    m = re.search(r"Inbox action:\s*(\w+)", desc or "")
    return (m.group(1) if m else "unknown").lower()


async def _list_members() -> list[dict]:
    tl_id = None
    data = await espo_get(
        "/TargetList",
        params={
            "searchParams": json.dumps(
                {
                    "maxSize": 1,
                    "select": ["id"],
                    "where": [
                        {"type": "equals", "attribute": "name", "value": TARGET_LIST_NAME}
                    ],
                }
            )
        },
    )
    rows = data.get("list") or []
    if rows:
        tl_id = rows[0]["id"]
    if not tl_id:
        raise SystemExit(f"TargetList {TARGET_LIST_NAME!r} not found")

    out, offset = [], 0
    while True:
        r = await espo_get(
            f"/TargetList/{tl_id}/leads",
            params={
                "maxSize": 200,
                "offset": offset,
                "select": "id,firstName,lastName,status,description",
            },
        )
        out.extend(r.get("list") or [])
        offset += 200
        if offset >= (r.get("total") or 0):
            break
    return out


async def cmd_list() -> None:
    members = await _list_members()
    by: dict[tuple[str, str], int] = {}
    for m in members:
        key = (_inbox_action(m.get("description")), m.get("status") or "?")
        by[key] = by.get(key, 0) + 1
    print(f"{TARGET_LIST_NAME}: {len(members)} leads")
    for (action, status), n in sorted(by.items()):
        print(f"  {action:10s} status={status:12s} {n}")


async def _touch_lead(lead: dict, *, dry_run: bool) -> str:
    lead_id = lead["id"]
    first = (lead.get("firstName") or "").strip() or "there"
    await espo_patch(f"/Lead/{lead_id}", {"status": "New" if dry_run else "In Process"})
    verb = "would contact" if dry_run else "contacted"
    await add_lead_note(
        lead_id,
        f"Newsletter nurture ({verb} via LinkedIn "
        f"{'dry run' if dry_run else 'sidecar'}).",
    )
    return lead_id


async def cmd_run(args) -> None:
    members = await _list_members()
    pending = [
        m
        for m in members
        if (m.get("status") or "") == "New"
        and _profile_url(m.get("description"))
        and (args.action == "all" or _inbox_action(m.get("description")) == args.action)
        and _inbox_action(m.get("description")) in ("message", "connect")
    ]
    print(f"pending ({args.action}): {len(pending)} — processing up to {args.limit}")
    pending = pending[: args.limit]

    dry_run = not args.approve
    sidecar = LinkedInSidecarClient()
    done = failed = skipped = 0
    for i, lead in enumerate(pending, 1):
        desc = lead.get("description") or ""
        url = _profile_url(desc)
        action = _inbox_action(desc)
        name = f"{lead.get('firstName') or ''} {lead.get('lastName') or ''}".strip()
        first = (lead.get("firstName") or "").strip() or "there"
        try:
            if action == "message":
                res = await sidecar.send_profile_message(
                    MESSAGE_TEMPLATE.format(first=first),
                    profile_url=url,
                    dry_run=dry_run,
                )
            else:
                res = await sidecar.send_connect(
                    profile_url=url,
                    note=CONNECT_NOTE_TEMPLATE.format(first=first),
                    dry_run=dry_run,
                )
            await _touch_lead(lead, dry_run=dry_run)
            print(f"[{i}] {action} {name}: {res.get('status')} {url}")
            done += 1
        except LinkedInSidecarError as e:
            skipped += 1
            print(f"[{i}] {action} {name}: sidecar {e.status_code} — {str(e)[:160]}")
            if "RATE_LIMITED" in str(e) or e.status_code == 429:
                print("circuit breaker open — stopping run")
                break
            if "NOT_CONNECTED" in str(e) or "NO_CONNECT" in str(e):
                await add_lead_note(lead["id"], f"Newsletter nurture skipped: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[{i}] {action} {name}: error {e}")
        if not dry_run and i < len(pending):
            pause = random.uniform(*MESSAGE_PAUSE)
            print(f"  sleeping {pause:.0f}s")
            await asyncio.sleep(pause)
    print(f"\ndone={done} skipped={skipped} failed={failed} dry_run={dry_run}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("--limit", type=int, default=5, help=f"max touches (cap {MAX_PER_RUN})")
    run.add_argument(
        "--action",
        choices=["message", "connect", "all"],
        default="all",
        help="which inbox-action lane to process",
    )
    g = run.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--approve", action="store_true", help="actually send")
    args = ap.parse_args()
    if getattr(args, "limit", 1) > MAX_PER_RUN:
        args.limit = MAX_PER_RUN
    asyncio.run(cmd_list() if args.cmd == "list" else cmd_run(args))


if __name__ == "__main__":
    main()
