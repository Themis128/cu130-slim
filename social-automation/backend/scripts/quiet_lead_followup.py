#!/usr/bin/env python3
"""Quiet-lead follow-up enrollment + status tool.

Adopts the Pipeline Optimization Toolkit 3-part "quiet lead" sequence
(24h gentle nudge → 3d pain reminder → 7d clean break) for leads that showed
interest in the free tech checkup but never booked. Enrolled leads are
stamped ``meta_data.quiet_lead = true`` and walked through
``nurture_sequence.QUIET_STEPS`` by the daily ``nurture-sweep`` Celery beat.

Runs inside the social-api container (or any env with DB access):

    docker exec social-api python3 /app/scripts/quiet_lead_followup.py enroll \
        --email owner@acme.gr --name "Nikos" --company "Acme SA"
    docker exec social-api python3 /app/scripts/quiet_lead_followup.py status
    docker exec social-api python3 /app/scripts/quiet_lead_followup.py unenroll --email owner@acme.gr

Commands:
    enroll      stamp a lead into the quiet lane (creates the Lead row when
                missing); re-enrolling resets the sequence to touch 1
    status      list quiet leads: step reached, enrolled-at, next due
    unenroll    pull a lead out of the quiet lane (sends stop immediately)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Self-locating: repo layout .../backend/scripts/x.py and container layout
# /app/scripts/x.py both resolve to the dir that owns the `app` package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402
from app.models.lead import Lead, LeadSource  # noqa: E402


def _team_id() -> uuid.UUID:
    from app.core.config import get_settings

    raw = getattr(get_settings(), "CLOUDLESS_WEB_ANALYTICS_TEAM_ID", "")
    if not raw:
        raise SystemExit("CLOUDLESS_WEB_ANALYTICS_TEAM_ID not configured")
    return uuid.UUID(str(raw))


async def _find_lead(db, team_id: uuid.UUID, email: str) -> Lead | None:
    q = select(Lead).where(Lead.team_id == team_id, Lead.email == email)
    return (await db.execute(q)).scalars().first()


async def enroll(args: argparse.Namespace) -> None:
    now = datetime.now(UTC).isoformat()
    async with async_session_maker() as db:
        lead = await _find_lead(db, _team_id(), args.email)
        if lead is None:
            if not args.name:
                raise SystemExit("lead not found — pass --name to create it")
            lead = Lead(
                team_id=_team_id(),
                source=LeadSource.website,
                name=args.name,
                email=args.email,
                meta_data={"form": "quiet-lead"},
            )
            db.add(lead)
            action = "created+enrolled"
        else:
            action = "re-enrolled (sequence reset)"
        meta = dict(lead.meta_data or {})
        meta.update({"quiet_lead": True, "quiet_lead_at": now, "quiet_step": 0})
        meta.pop("quiet_step_at", None)
        if args.company:
            meta["company"] = args.company
        if args.name and lead.name != args.name:
            lead.name = args.name
        lead.meta_data = meta
        await db.commit()
    print(f"{action}: {args.email} — touch 1 fires at the next daily sweep (>=24h)")


async def unenroll(args: argparse.Namespace) -> None:
    async with async_session_maker() as db:
        lead = await _find_lead(db, _team_id(), args.email)
        if lead is None:
            raise SystemExit(f"no lead with email {args.email}")
        meta = dict(lead.meta_data or {})
        meta["quiet_lead"] = False
        lead.meta_data = meta
        await db.commit()
        from app.services.nurture_sequence import _cf_shadow_call  # noqa: E402

        await _cf_shadow_call(
            "LEAD_SCHEDULER_URL", "LEAD_SCHEDULER_TOKEN",
            f"/do/followup/{lead.id}/unsubscribe", {"email": lead.email},
        )
    print(f"unenrolled: {args.email}")


async def status(_args: argparse.Namespace) -> None:
    from app.services.nurture_sequence import QUIET_STEPS, _due_quiet_step, _quiet_age_days

    async with async_session_maker() as db:
        q = select(Lead).where(Lead.team_id == _team_id())
        leads = (await db.execute(q)).scalars().all()

    rows = [lead for lead in leads if (lead.meta_data or {}).get("quiet_lead")]
    if not rows:
        print("no quiet-lane leads enrolled")
        return
    for lead in sorted(rows, key=lambda x: x.email or ""):
        meta = lead.meta_data or {}
        due = _due_quiet_step(lead)
        nxt = f"next: touch {due['step']} ({due['key']}) due now" if due else "sequence complete / not yet due"
        print(f"{lead.email:<40} step={meta.get('quiet_step', 0)}/{len(QUIET_STEPS)} age={_quiet_age_days(lead):.1f}d  {nxt}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enroll", help="enroll a lead into the quiet-lead sequence")
    e.add_argument("--email", required=True)
    e.add_argument("--name")
    e.add_argument("--company")
    e.set_defaults(fn=enroll)

    u = sub.add_parser("unenroll", help="remove a lead from the quiet-lead sequence")
    u.add_argument("--email", required=True)
    u.set_defaults(fn=unenroll)

    s = sub.add_parser("status", help="list quiet-lane leads and their next due touch")
    s.set_defaults(fn=status)

    args = p.parse_args()
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
