#!/usr/bin/env python3
# ruff: noqa: E402
"""ops_incident.py — report/list/resolve ops incidents for the AgentOps loop.

Any detector (host watchdog, compose-healer, session healer, ad-hoc script)
runs this inside the social-api container to feed the ops-agent sweep:

    docker exec social-api python3 /app/scripts/ops_incident.py report \
        --source compose-watchdog --kind container-down \
        --target social-api --severity critical \
        --title "social-api exited unexpectedly" \
        --detail '{"exit_code":1}'

Commands:
    report     upsert an incident (dedupes on source|kind|target fingerprint)
    list       show incidents, newest first (--status open|resolved|all)
    resolve    mark an open incident resolved by fingerprint prefix/id
    sweep      run the ops-agent sweep once, synchronously (debug)

The Celery task `ops_agent_sweep` runs every 5 min and does the triage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from app.db.session import async_session_maker  # noqa: E402
from app.models.ops_incident import IncidentSeverity, IncidentStatus, OpsIncident  # noqa: E402


async def _report(args) -> None:
    from app.services.ops_agent import report_incident

    async with async_session_maker() as db:
        inc = await report_incident(
            db,
            source=args.source,
            kind=args.kind,
            title=args.title,
            target=args.target,
            severity=args.severity,
            detail=json.loads(args.detail) if args.detail else {},
        )
        await db.commit()
    print(f"incident {inc.id} status={inc.status.value} fp={inc.fingerprint[:12]} recur={inc.recurrence_count}")


async def _list(args) -> None:
    from sqlalchemy import select

    q = select(OpsIncident).order_by(OpsIncident.last_seen.desc()).limit(args.limit)
    if args.status != "all":
        q = q.where(OpsIncident.status == IncidentStatus(args.status))
    async with async_session_maker() as db:
        rows = (await db.execute(q)).scalars().all()
    for r in rows:
        print(f"{r.last_seen:%Y-%m-%d %H:%M} {r.status.value:9} {r.severity.value:8} {r.source}/{r.kind} {r.target or '-'} :: {r.title[:90]}")
        if r.rca:
            print(f"    rca: {r.rca[:200]}")
        if r.action_taken or r.proposed_action:
            print(f"    act: {r.action_taken or r.proposed_action} -> {r.action_result[:150]}")


async def _resolve(args) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import String, cast, select

    q = select(OpsIncident).where(OpsIncident.status != IncidentStatus.RESOLVED)
    if args.id_or_fp:
        q = q.where((cast(OpsIncident.id, String) == args.id_or_fp) | (OpsIncident.fingerprint.startswith(args.id_or_fp)))
    async with async_session_maker() as db:
        rows = (await db.execute(q)).scalars().all()
        for r in rows:
            r.status = IncidentStatus.RESOLVED
            r.resolved_at = datetime.now(UTC)
        await db.commit()
    print(f"resolved {len(rows)} incident(s)")


async def _sweep(args) -> None:
    from app.services.ops_agent import sweep

    async with async_session_maker() as db:
        stats = await sweep(db)
    print(json.dumps(stats))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="report an incident")
    r.add_argument("--source", required=True)
    r.add_argument("--kind", required=True)
    r.add_argument("--title", required=True)
    r.add_argument("--target", default="")
    r.add_argument("--severity", default="ops", choices=[s.value for s in IncidentSeverity])
    r.add_argument("--detail", default="", help="JSON object")
    r.set_defaults(fn=_report)

    ls = sub.add_parser("list", help="list incidents")
    ls.add_argument("--status", default="open", choices=["open", "triaged", "actioned", "resolved", "escalated", "all"])
    ls.add_argument("--limit", type=int, default=20)
    ls.set_defaults(fn=_list)

    rs = sub.add_parser("resolve", help="resolve matching open incidents")
    rs.add_argument("id_or_fp", nargs="?", default="")
    rs.set_defaults(fn=_resolve)

    sw = sub.add_parser("sweep", help="run one ops-agent sweep now")
    sw.set_defaults(fn=_sweep)

    args = p.parse_args()
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
