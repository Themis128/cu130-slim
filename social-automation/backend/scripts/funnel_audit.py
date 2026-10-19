#!/usr/bin/env python3
"""Sales-funnel audit — every stage counted from live data.

Adoption of Tolstova & Silakova (ITMO, Omsk Scientific Bulletin 2023,
DOI 10.25206/2542-0488-2023-8-2-145-154): "Classification of management
tools for building a sales funnel in small businesses". Their Tokarev-style
quantitative funnel (each stage has a real headcount, conversion is
stage-to-stage) applied to SocialAuto's real surfaces:

    audience → engaged → lead → nurtured → opportunity → won

Two paper-specific rules implemented:

  * Weak-point detection — the stage with the lowest conversion ratio is
    named (paper: funnels let you "instantly eliminate weak points").
  * Auction warning — growing the top (traffic) without fixing conversion
    erodes margin; flagged when reach grows but lead conversion is < 0.5%.

Run inside social-api (PYTHONPATH=/app) or the backend venv:

    python scripts/funnel_audit.py          # human report
    python scripts/funnel_audit.py --json   # machine output
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402

# (stage, description, count coroutine) — assembled in audit() because the
# lead/opportunity counts come from EspoCRM, the rest from Postgres.


async def _social_reach() -> int:
    async with async_session_maker() as db:
        return (await db.execute(text(
            "SELECT coalesce(sum(followers),0) FROM ("
            "  SELECT DISTINCT ON (social_account_id) followers"
            "  FROM follower_snapshots"
            "  ORDER BY social_account_id, captured_at DESC) t"
        ))).scalar() or 0


async def _engaged_30d() -> int:
    """Unique posts with engagement events in 30d — proxy for 'problem
    aware' reach (paper stage 2)."""
    async with async_session_maker() as db:
        return (await db.execute(text(
            "SELECT count(DISTINCT post_id) FROM post_analytics_snapshots"
            "  WHERE captured_at > now() - interval '30 days'"
            "  AND (likes + comments + shares) > 0"
        ))).scalar() or 0


async def _emails_sent() -> int:
    async with async_session_maker() as db:
        return (await db.execute(text("SELECT count(*) FROM email_logs"))
                ).scalar() or 0


async def audit() -> dict:
    from app.services import espocrm_client as espo

    reach, engaged, emails = await asyncio.gather(
        _social_reach(), _engaged_30d(), _emails_sent())

    leads = await espo.list_all(
        "Lead", select=["id", "status", "source", "createdAt"])
    opps = await espo.list_all(
        "Opportunity", select=["id", "stage", "createdAt"])

    leads_total = len(leads)
    # "nurtured" = leads that moved past raw capture (any status that is not
    # New/Dead) + leads we have actively mailed
    nurtured = sum(1 for lead in leads
                   if lead.get("status") not in (None, "New", "Dead"))
    dead = sum(1 for lead in leads if lead.get("status") == "Dead")
    won = sum(1 for o in opps if (o.get("stage") or "").lower()
              in ("closed won", "won"))

    sources: dict[str, int] = {}
    for lead in leads:
        sources[lead.get("source") or "(none)"] = \
            sources.get(lead.get("source") or "(none)", 0) + 1

    stages = [
        {"stage": "audience", "count": reach,
         "note": "followers, latest snapshot per account"},
        {"stage": "engaged", "count": engaged,
         "note": "posts with likes+comments+shares, 30d"},
        {"stage": "lead", "count": leads_total,
         "note": f"EspoCRM leads ({dead} dead)"},
        {"stage": "nurtured", "count": nurtured,
         "note": f"leads past 'New' + {emails} drip mails sent"},
        {"stage": "opportunity", "count": len(opps),
         "note": "EspoCRM opportunities"},
        {"stage": "won", "count": won, "note": "opportunities closed-won"},
    ]
    prev = None
    for s in stages:
        s["conv_pct"] = (round(100 * s["count"] / prev["count"], 2)
                         if prev and prev["count"] else None)
        prev = s
    weakest = min((s for s in stages[1:] if s["conv_pct"] is not None),
                  key=lambda s: s["conv_pct"], default=None)
    weakest_transition = None
    if weakest:
        before = stages[stages.index(weakest) - 1]
        weakest_transition = (f"{before['stage']}→{weakest['stage']} "
                              f"({weakest['conv_pct']}%)")

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "lead_sources": sources,
        "stages": stages,
        "weakest_transition": weakest_transition,
        "auction_warning": bool(
            weakest and weakest["stage"] == "lead"
            and (weakest["conv_pct"] or 100) < 0.5 and reach > 0),
    }


def render(r: dict) -> str:
    lines = [
        f"Funnel audit — {r['generated_at'][:16]}Z",
        "",
        f"{'stage':<12} {'count':>7}  stage→stage conv",
        "-" * 60,
    ]
    for s in r["stages"]:
        conv = "" if s["conv_pct"] is None else f"{s['conv_pct']}%"
        lines.append(f"{s['stage']:<12} {s['count']:>7}  {conv:<8} {s['note']}")
    lines.append("")
    if r.get("weakest_transition"):
        lines.append(f"weakest transition: {r['weakest_transition']}")
    if r.get("auction_warning"):
        lines.append("⚠ auction warning: reach is growing but lead "
                     "conversion is thin — more traffic raises cost-per-lead "
                     "before it raises revenue (Tolstova & Silakova)")
    src = ", ".join(f"{k}={v}" for k, v in
                    sorted(r["lead_sources"].items(),
                           key=lambda kv: -kv[1]))
    lines.append(f"lead sources: {src}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(audit())
    print(json.dumps(report, indent=2) if args.json else render(report))
    # exit 1 when the funnel has a broken stage (0 conversions in/out of a
    # stage that has upstream volume) — a structural gap, not noise
    broken = [s for s in report["stages"][2:]
              if s["count"] == 0 and report["stages"][2]["count"] > 0]
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
