#!/usr/bin/env python3
"""Engagement-effectiveness audit — the paper's three KPIs on live data.

Adoption of Bening & Roostika, "Optimizing Social Media and Paid Advertising
in Digital Marketing Strategy to Increase Branding and Customer Engagement"
(Kontigensi: Jurnal Ilmu Manajemen, vol. 13 no. 1, June 2025, pp. 409-423,
DOI 10.56457/jimk.v13i1.765) — a PT KB Valbury Sekuritas case study that
evaluates digital marketing via three indicators and prescribes five
optimizations:

  KPIs      — engagement rate, conversion rate, customer feedback
  Levers    — interactive formats (polls/Q&A/live), schedule consistency +
              trend usage, skilled team, paid ads on all relevant platforms
              evaluated periodically, central↔local content fit
  Challenge — content must grab attention in the first seconds and adapt to
              the audience's stage of the customer journey

Mapped onto SocialAuto's real surfaces (no heuristics — every number is a
live query):

  engagement rate → post_analytics_snapshots (per-post latest, 30d)
  conversion      → web_analytics_events with utm_source + EspoCRM leads
  feedback        → comments + shares volume on published posts, 30d
  interactivity   → share of published posts carrying a question/CTA hook
  consistency     → published posts per platform per week vs 14d silence
  paid evaluation → ad_campaign_snapshots recency (daily linkedin-ads-report
                    beat should keep it < 48h)

Run inside social-api (PYTHONPATH=/app) or the backend venv:

    python scripts/engagement_audit.py          # human report
    python scripts/engagement_audit.py --json   # machine output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402

# Paper lever 1 — interactive content (polls, Q&A, "comment below", lives).
# A post counts as interactive when its copy explicitly asks for a response.
_INTERACTIVE_RE = re.compile(
    r"\?|comment below|tell me|share your|poll:|which one|agree\?|thoughts\?|reply with|drop a|vote",
    re.IGNORECASE,
)


async def _engagement_by_platform() -> dict[str, dict]:
    """Latest snapshot per post, 30d — the paper's engagement-rate KPI.

    post_analytics_snapshots appends a row per sync, so dedupe to the most
    recent capture per post before aggregating."""
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT platform,"
                    "       count(*) AS posts,"
                    "       coalesce(sum(impressions),0) AS impressions,"
                    "       coalesce(sum(likes+comments+shares),0) AS engagements,"
                    "       coalesce(sum(comments),0) AS comments,"
                    "       coalesce(sum(shares),0) AS shares"
                    "  FROM ("
                    "    SELECT DISTINCT ON (coalesce(post_id::text, platform_post_id))"
                    "           platform, impressions, likes, comments, shares"
                    "      FROM post_analytics_snapshots"
                    "     WHERE captured_at > now() - interval '30 days'"
                    "     ORDER BY coalesce(post_id::text, platform_post_id), captured_at DESC"
                    "  ) latest"
                    " GROUP BY platform ORDER BY platform"
                )
            )
        ).all()
    out = {}
    for platform, posts, impressions, engagements, comments, shares in rows:
        out[platform] = {
            "posts": posts,
            "impressions": impressions,
            "engagements": engagements,
            "comments": comments,
            "shares": shares,
            "er_pct": round(100 * engagements / impressions, 2) if impressions else None,
        }
    return out


async def _conversion_30d() -> dict:
    """UTM-attributed site events + fresh EspoCRM leads — the paper's
    conversion-rate KPI (audience → customer action, not vanity reach)."""
    async with async_session_maker() as db:
        attributed = (
            await db.execute(text("SELECT count(*) FROM web_analytics_events WHERE occurred_at > now() - interval '30 days'   AND payload ? 'utm_source'"))
        ).scalar() or 0
    from app.services import espocrm_client as espo

    leads = await espo.list_all("Lead", select=["id", "createdAt", "source"], max_items=25000)
    cutoff = datetime.now(UTC).timestamp() - 30 * 86400
    fresh = [
        lead
        for lead in leads
        if (lead.get("createdAt") or "") and lead["createdAt"][:19].replace(" ", "T") >= datetime.fromtimestamp(cutoff, UTC).isoformat()[:19]
    ]
    return {
        "attributed_events_30d": attributed,
        "leads_30d": len(fresh),
        "note": "UTM events = audience→site action; leads = capture. Both live counts.",
    }


async def _published_cadence() -> dict[str, dict]:
    """Published posts per platform over the last 4 weeks — the paper's
    consistency lever. Silence ≥14d on an active account is a gap."""
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT sa.platform, count(*) AS n,"
                    "       max(pt.published_at) AS last_pub"
                    "  FROM post_targets pt"
                    "  JOIN social_accounts sa ON sa.id = pt.social_account_id"
                    " WHERE pt.status = 'published'"
                    "   AND pt.published_at > now() - interval '28 days'"
                    " GROUP BY sa.platform ORDER BY sa.platform"
                )
            )
        ).all()
        # Platforms with an active account but zero publishes in 28d.
        # Messaging channels (telegram, whatsapp, viber) publish via the
        # send endpoints, logged as analytics_events channel_post/bot_reply —
        # post_targets is feed-only, so check that surface before flagging.
        silent = (
            await db.execute(
                text(
                    "SELECT DISTINCT sa.platform FROM social_accounts sa"
                    " WHERE sa.status = 'active'"
                    "   AND sa.platform NOT IN ("
                    "     SELECT DISTINCT sa2.platform FROM post_targets pt"
                    "     JOIN social_accounts sa2 ON sa2.id = pt.social_account_id"
                    "     WHERE pt.status='published'"
                    "       AND pt.published_at > now() - interval '28 days')"
                    "   AND sa.platform NOT IN ("
                    "     SELECT DISTINCT sa3.platform FROM analytics_events ae"
                    "     JOIN social_accounts sa3 ON sa3.id = ae.social_account_id"
                    "     WHERE ae.event_type IN ('channel_post','bot_reply')"
                    "       AND ae.occurred_at > now() - interval '28 days')"
                    " ORDER BY sa.platform"
                )
            )
        ).all()
        # Messaging-channel send activity, surfaced alongside feed cadence
        sends = (
            await db.execute(
                text(
                    "SELECT sa.platform, count(*) AS n, max(ae.occurred_at) AS last_send"
                    "  FROM analytics_events ae"
                    "  JOIN social_accounts sa ON sa.id = ae.social_account_id"
                    " WHERE ae.event_type = 'channel_post'"
                    "   AND ae.occurred_at > now() - interval '28 days'"
                    " GROUP BY sa.platform ORDER BY sa.platform"
                )
            )
        ).all()
    out = {r[0]: {"posts_28d": r[1], "per_week": round(r[1] / 4, 1), "last": str(r[2])[:10]} for r in rows}
    for platform, n, last_send in sends:
        out[f"{platform} (sends)"] = {"posts_28d": n, "per_week": round(n / 4, 1), "last": str(last_send)[:10]}
    out["_silent_accounts"] = [r[0] for r in silent]
    return out


async def _interactivity_30d() -> dict:
    """Share of published copy that explicitly invites a response — the
    paper's first recommendation (polls, Q&A, comment prompts)."""
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                text("SELECT content_text FROM posts WHERE status = 'published'   AND published_at > now() - interval '30 days'   AND content_text IS NOT NULL")
            )
        ).all()
    total = len(rows)
    interactive = sum(1 for (body,) in rows if body and _INTERACTIVE_RE.search(body))
    return {
        "posts_30d": total,
        "interactive": interactive,
        "pct": round(100 * interactive / total, 1) if total else None,
    }


async def _paid_eval_freshness() -> dict:
    """ad_campaign_snapshots recency — the paper's 'evaluate paid ads
    periodically' lever. The daily linkedin-ads-report beat should keep the
    newest snapshot under ~48h; staleness means paid spend flies blind."""
    async with async_session_maker() as db:
        row = (await db.execute(text("SELECT max(captured_at), count(*) FROM ad_campaign_snapshots WHERE captured_at > now() - interval '30 days'"))).first()
        campaigns = (
            await db.execute(text("SELECT count(DISTINCT campaign_id) FROM ad_campaign_snapshots WHERE captured_at > now() - interval '30 days'"))
        ).scalar() or 0
    if not row or not row[0]:
        return {"snapshots_30d": 0, "campaigns": 0, "age_hours": None, "fresh": False}
    age_h = round((datetime.now(UTC) - row[0]).total_seconds() / 3600, 1)
    return {
        "snapshots_30d": row[1],
        "campaigns": campaigns,
        "age_hours": age_h,
        "fresh": age_h <= 48,
    }


async def audit() -> dict:
    engagement, conversion, cadence, interactivity, paid = await asyncio.gather(
        _engagement_by_platform(),
        _conversion_30d(),
        _published_cadence(),
        _interactivity_30d(),
        _paid_eval_freshness(),
    )

    gaps = []
    for platform, m in engagement.items():
        if m["posts"] >= 3 and (m["er_pct"] or 0) < 0.5:
            gaps.append(f"low engagement-rate on {platform}: {m['er_pct']}% over {m['posts']} posts")
    for platform in cadence.pop("_silent_accounts"):
        gaps.append(f"silent platform: {platform} has an active account, zero publishes in 28d")
    if interactivity["posts_30d"] and (interactivity["pct"] or 0) < 40:
        gaps.append(f"interactivity {interactivity['pct']}% — the paper's top fix is response-inviting formats (polls/Q&A/comment prompts)")
    if not paid["fresh"]:
        gaps.append(
            f"paid-ads evaluation stale ({paid['age_hours']}h since last snapshot)"
            if paid["age_hours"] is not None
            else "paid-ads evaluation missing — no snapshots in 30d while spend is active"
        )
    if conversion["attributed_events_30d"] == 0:
        gaps.append("attribution silent — zero UTM events in 30d (see leadgen-ops)")

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "kpis": {
            "engagement_rate": engagement,
            "conversion": conversion,
            "feedback": {p: {"comments": m["comments"], "shares": m["shares"]} for p, m in engagement.items()},
        },
        "levers": {
            "interactivity_30d": interactivity,
            "publish_cadence": cadence,
            "paid_eval": paid,
        },
        "gaps": gaps,
    }


def render(r: dict) -> str:
    lines = [
        f"Engagement audit — {r['generated_at'][:16]}Z",
        "",
        "KPI: engagement rate (latest per post, 30d)",
        f"{'platform':<12} {'posts':>5} {'impr':>8} {'eng':>6} {'ER%':>6} {'comments':>8} {'shares':>6}",
        "-" * 60,
    ]
    for platform, m in sorted(r["kpis"]["engagement_rate"].items()):
        er = "-" if m["er_pct"] is None else f"{m['er_pct']}"
        lines.append(f"{platform:<12} {m['posts']:>5} {m['impressions']:>8} {m['engagements']:>6} {er:>6} {m['comments']:>8} {m['shares']:>6}")
    conv = r["kpis"]["conversion"]
    lines += [
        "",
        f"KPI: conversion — {conv['attributed_events_30d']} UTM events, {conv['leads_30d']} new leads (30d)",
        "",
        "Levers",
        "-" * 60,
    ]
    inter = r["levers"]["interactivity_30d"]
    lines.append(f"interactivity: {inter['interactive']}/{inter['posts_30d']} posts ({inter['pct']}%) invite a response")
    for platform, m in sorted(r["levers"]["publish_cadence"].items()):
        lines.append(f"cadence {platform:<12} {m['posts_28d']:>3}/28d ({m['per_week']}/wk) last {m['last']}")
    paid = r["levers"]["paid_eval"]
    lines.append(
        f"paid-eval: {'fresh' if paid['fresh'] else 'STALE'} ({paid['snapshots_30d']} snapshots, {paid['campaigns']} campaigns, age {paid['age_hours']}h)"
    )
    lines.append("")
    if r["gaps"]:
        lines.append("gaps:")
        lines += [f"  • {g}" for g in r["gaps"]]
    else:
        lines.append("gaps: none")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(audit())
    print(json.dumps(report, indent=2) if args.json else render(report))
    sys.exit(1 if report["gaps"] else 0)


if __name__ == "__main__":
    main()
