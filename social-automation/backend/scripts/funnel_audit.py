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


async def _social_reach() -> tuple[int, int]:
    """(latest followers summed per account, same sum ~30d ago).

    The trailing figure makes the auction warning a real growth check —
    the paper's warning is about reach *growing* while conversion is thin.
    """
    sql = (
        "SELECT coalesce(sum(followers),0) FROM ("
        "  SELECT DISTINCT ON (social_account_id) followers"
        "  FROM follower_snapshots {where}"
        "  ORDER BY social_account_id, captured_at DESC) t"
    )
    async with async_session_maker() as db:
        latest = (await db.execute(text(sql.format(where="")))).scalar() or 0
        prev = (await db.execute(text(sql.format(where="WHERE captured_at <= now() - interval '30 days'")))).scalar() or 0
        return latest, prev


async def _engaged_30d() -> int:
    """Unique posts with engagement events in 30d — ACTIVITY PROXY for
    'problem aware' reach (paper stage 2), not a count of people.

    post_analytics_snapshots has no actor identity, and discovered posts
    carry platform_post_id instead of post_id — so this counts posts,
    keyed by whichever id is present."""
    async with async_session_maker() as db:
        return (
            await db.execute(
                text(
                    "SELECT count(DISTINCT coalesce(post_id::text, platform_post_id))"
                    "  FROM post_analytics_snapshots"
                    "  WHERE captured_at > now() - interval '30 days'"
                    "  AND (likes + comments + shares) > 0"
                )
            )
        ).scalar() or 0


async def _drip_sends() -> int:
    """Campaign recipient rows actually sent — real drip volume, not the
    transactional email_logs stream (which includes failures/system mail)."""
    async with async_session_maker() as db:
        return (await db.execute(text("SELECT count(*) FROM email_recipients WHERE status = 'sent'"))).scalar() or 0


async def audit() -> dict:
    from app.services import espocrm_client as espo

    (reach, reach_30d_ago), engaged, drip_sends = await asyncio.gather(_social_reach(), _engaged_30d(), _drip_sends())

    # espo.list_all caps at max_items; a truncated pull would show partial
    # stage totals as complete — surface it in the note instead of hiding it.
    MAX = 25000
    leads = await espo.list_all("Lead", select=["id", "status", "source", "createdAt"], max_items=MAX)
    opps = await espo.list_all("Opportunity", select=["id", "stage", "createdAt"], max_items=MAX)
    truncated = len(leads) >= MAX or len(opps) >= MAX

    leads_total = len(leads)
    # "nurtured" = leads that moved past raw capture (any status that is not
    # New/Dead) + leads we have actively mailed
    nurtured = sum(1 for lead in leads if lead.get("status") not in (None, "New", "Dead"))
    dead = sum(1 for lead in leads if lead.get("status") == "Dead")
    # active pipeline excludes both closed outcomes — closed-lost is history,
    # closed-won is reported separately in the 'won' stage
    active_opps = sum(1 for o in opps if (o.get("stage") or "").lower() not in ("closed won", "closed lost", "won", "lost"))
    won = sum(1 for o in opps if (o.get("stage") or "").lower() in ("closed won", "won"))

    sources: dict[str, int] = {}
    for lead in leads:
        sources[lead.get("source") or "(none)"] = sources.get(lead.get("source") or "(none)", 0) + 1
    trunc_note = " (list truncated at cap)" if truncated else ""

    stages = [
        {"stage": "audience", "count": reach, "note": f"followers, latest per account ({reach_30d_ago} 30d ago)"},
        # engaged is a post-activity proxy — never a person count; excluded
        # from weakest-transition comparison (unit mismatch, Devin Review)
        {"stage": "engaged", "count": engaged, "proxy": True, "note": "posts with likes+comments+shares, 30d — activity proxy, not engaged people"},
        {"stage": "lead", "count": leads_total, "note": f"EspoCRM leads ({dead} dead){trunc_note}"},
        {"stage": "nurtured", "count": nurtured, "note": f"leads past 'New' + {drip_sends} campaign sends"},
        {"stage": "opportunity", "count": active_opps, "note": f"EspoCRM opportunities, active only{trunc_note}"},
        {"stage": "won", "count": won, "note": "opportunities closed-won"},
    ]
    prev = None
    for s in stages:
        s["conv_pct"] = round(100 * s["count"] / prev["count"], 2) if prev and prev["count"] else None
        prev = s
    # weakest transition only compares like units — a proxy stage's conv_pct
    # is displayed for context but never diagnosed as the weak link
    weakest = min(
        (s for i, s in enumerate(stages[1:], start=1) if s["conv_pct"] is not None and not s.get("proxy") and not stages[i - 1].get("proxy")),
        key=lambda s: s["conv_pct"],
        default=None,
    )
    weakest_transition = None
    if weakest:
        before = stages[stages.index(weakest) - 1]
        weakest_transition = f"{before['stage']}→{weakest['stage']} ({weakest['conv_pct']}%)"

    # auction warning needs *measured* growth, not just a large current reach
    reach_growing = reach_30d_ago > 0 and reach > reach_30d_ago
    lead_conv_vs_audience = (100 * leads_total / reach) if reach else 0.0
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "lead_sources": sources,
        "stages": stages,
        "weakest_transition": weakest_transition,
        "auction_warning": bool(reach_growing and lead_conv_vs_audience < 0.5),
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
        if s.get("proxy"):
            conv = "(proxy)"
        lines.append(f"{s['stage']:<12} {s['count']:>7}  {conv:<8} {s['note']}")
    lines.append("")
    if r.get("weakest_transition"):
        lines.append(f"weakest transition: {r['weakest_transition']}")
    if r.get("auction_warning"):
        lines.append(
            "⚠ auction warning: follower reach grew over 30d while "
            "lead conversion vs audience is <0.5% — more traffic "
            "raises cost-per-lead before it raises revenue "
            "(Tolstova & Silakova)"
        )
    src = ", ".join(f"{k}={v}" for k, v in sorted(r["lead_sources"].items(), key=lambda kv: -kv[1]))
    lines.append(f"lead sources: {src}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(audit())
    print(json.dumps(report, indent=2) if args.json else render(report))
    # exit 1 when the funnel has a broken stage — any stage at zero while
    # the stage before it has volume (e.g. engaged posts but zero leads)
    broken = [s for i, s in enumerate(report["stages"][1:], start=1) if s["count"] == 0 and report["stages"][i - 1]["count"] > 0]
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
