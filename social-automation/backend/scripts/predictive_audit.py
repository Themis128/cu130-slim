#!/usr/bin/env python3
"""Predictive-analytics readiness audit — Adesina et al. on live data.

Adoption of Adesina, Iyelolu & Paul, "Leveraging predictive analytics for
strategic decision-making" (World Journal of Advanced Research and Reviews
22(3), 2024, pp. 1927-1934, DOI 10.30574/wjarr.2024.22.3.1961). The paper's
chain: high-quality data → analytical techniques → validated models →
predictions that drive decisions. Its prerequisites map onto SocialAuto's
predictive surfaces:

  data quality      — snapshot coverage + anomaly-free series
                      (the paper: no model compensates for bad data)
  best-hour model   — insights_engine shrunk-bucket best_hour_athens needs
                      enough observations per platform×hour to beat the
                      static heuristic it shrinks toward (k=4 shrinkage)
  growth velocity   — follower_snapshots slope → monetization threshold ETA
                      (days_to_goal); a stalled series means "not growing
                      at current pace" — prediction says don't plan on it
  momentum          — recent-vs-prior follower deltas; absurd jumps mean
                      corrupted series, not momentum (follower-analytics-
                      guard methodology)

Live checks — every number is a query, no heuristics:

    python scripts/predictive_audit.py          # human report
    python scripts/predictive_audit.py --json   # machine output
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

# Observations below this per platform can't produce a shrunk-bucket hour
# estimate that beats the static heuristic — prediction stays baseline.
_MIN_HOURLY_OBS = 12
# Monetization goal ETAs are only meaningful when the 14d velocity is
# positive; at zero/negative the honest output is "not growing".
_FB_STARS_GOAL = 500


async def _hourly_sufficiency() -> dict[str, dict]:
    """Post-analytics observations per platform over 60d — the training
    volume behind the shrunk-bucket best-hour estimate."""
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT platform, count(*) AS n,"
                    "       count(DISTINCT date_trunc('day', captured_at)) AS days,"
                    "       count(DISTINCT extract(hour from captured_at)) AS hours"
                    "  FROM post_analytics_snapshots"
                    " WHERE captured_at > now() - interval '60 days'"
                    " GROUP BY platform ORDER BY platform"
                )
            )
        ).all()
    return {
        p: {"observations": n, "days": d, "distinct_hours": h, "sufficient": n >= _MIN_HOURLY_OBS}
        for p, n, d, h in rows
    }


async def _growth_velocity() -> list[dict]:
    """Latest-vs-14d-ago follower count per account → daily velocity and
    (for facebook) days to the FB Stars 500 threshold at current pace."""
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT sa.platform, sa.username,"
                    "       first.followers AS latest,"
                    "       prev.followers AS prior"
                    "  FROM social_accounts sa"
                    "  JOIN LATERAL ("
                    "    SELECT followers FROM follower_snapshots fs"
                    "     WHERE fs.social_account_id = sa.id"
                    "     ORDER BY captured_at DESC LIMIT 1"
                    "  ) first ON true"
                    "  LEFT JOIN LATERAL ("
                    "    SELECT followers FROM follower_snapshots fs"
                    "     WHERE fs.social_account_id = sa.id"
                    "       AND captured_at <= now() - interval '14 days'"
                    "     ORDER BY captured_at DESC LIMIT 1"
                    "  ) prev ON true"
                    " WHERE sa.status = 'active'"
                    " ORDER BY sa.platform"
                )
            )
        ).all()
    out = []
    for platform, username, latest, prior in rows:
        velocity = round((latest - prior) / 14, 2) if prior is not None else None
        eta = None
        if platform == "facebook" and velocity and velocity > 0 and latest < _FB_STARS_GOAL:
            eta = round((_FB_STARS_GOAL - latest) / velocity)
        out.append({
            "platform": platform,
            "account": f"{platform}/@{username}" if username else platform,
            "followers": latest,
            "per_day": velocity,
            "eta_fb_stars_days": eta,
            "stalled": velocity is not None and velocity <= 0,
        })
    return out


async def _momentum_anomalies() -> list[str]:
    """Accounts whose 7d delta swings >50% of the current count — the
    corrupted-series signature, not real momentum (predictions on these
    are worse than no prediction, per the paper's data-quality rule)."""
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT sa.platform, sa.username, first.followers AS latest,"
                    "       prev.followers AS prior"
                    "  FROM social_accounts sa"
                    "  JOIN LATERAL ("
                    "    SELECT followers FROM follower_snapshots fs"
                    "     WHERE fs.social_account_id = sa.id"
                    "     ORDER BY captured_at DESC LIMIT 1) first ON true"
                    "  JOIN LATERAL ("
                    "    SELECT followers FROM follower_snapshots fs"
                    "     WHERE fs.social_account_id = sa.id"
                    "       AND captured_at <= now() - interval '7 days'"
                    "     ORDER BY captured_at DESC LIMIT 1) prev ON true"
                    " WHERE sa.status = 'active'"
                )
            )
        ).all()
    return [
        f"{p}/@{u or '?'}: {latest - prior:+d} in 7d vs base {prior}"
        for p, u, latest, prior in rows
        # >50% swing AND a meaningful absolute delta — tiny bases
        # (prior=1, +1 follower) are noise, not corruption
        if prior and abs(latest - prior) > max(50, 0.5 * prior)
    ]


async def _snapshot_coverage() -> dict:
    """Do we have enough history for any model? Min days of follower +
    post-analytics history per the paper's data-quality prerequisite."""
    async with async_session_maker() as db:
        f_days = (
            await db.execute(
                text("SELECT count(DISTINCT captured_at::date) FROM follower_snapshots")
            )
        ).scalar() or 0
        p_days = (
            await db.execute(
                text("SELECT count(DISTINCT captured_at::date) FROM post_analytics_snapshots")
            )
        ).scalar() or 0
    return {"follower_history_days": f_days, "analytics_history_days": p_days}


async def audit() -> dict:
    hourly, velocity, anomalies, coverage = await asyncio.gather(
        _hourly_sufficiency(), _growth_velocity(), _momentum_anomalies(), _snapshot_coverage()
    )

    gaps = []
    for p, m in hourly.items():
        if not m["sufficient"]:
            gaps.append(
                f"{p}: {m['observations']} observations — best-hour estimate "
                "shrinks to static heuristic (prediction is baseline, not learned)"
            )
    for v in velocity:
        if v["stalled"]:
            gaps.append(f"{v['account']}: growth stalled ({v['per_day']}/day) — threshold ETA undefined")
    for a in anomalies:
        gaps.append(f"corrupted follower series {a} — exclude from predictions until guarded")
    if coverage["follower_history_days"] < 30:
        gaps.append(f"only {coverage['follower_history_days']}d of follower history — too thin for trend models")

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_coverage": coverage,
        "best_hour_training": hourly,
        "growth_velocity": velocity,
        "momentum_anomalies": anomalies,
        "gaps": gaps,
    }


def render(r: dict) -> str:
    lines = [
        f"Predictive readiness — {r['generated_at'][:16]}Z",
        "",
        f"history: {r['data_coverage']['follower_history_days']}d followers, "
        f"{r['data_coverage']['analytics_history_days']}d post analytics",
        "",
        "best-hour training volume (60d)",
        f"{'platform':<12} {'obs':>5} {'days':>5} {'hours':>5}  sufficient?",
        "-" * 45,
    ]
    for p, m in sorted(r["best_hour_training"].items()):
        lines.append(f"{p:<12} {m['observations']:>5} {m['days']:>5} {m['distinct_hours']:>5}  {'yes' if m['sufficient'] else 'NO'}")
    lines += ["", "growth velocity (14d)", "-" * 45]
    for v in r["growth_velocity"]:
        vel = "n/a" if v["per_day"] is None else f"{v['per_day']:+.2f}/day"
        eta = f"  → {v['eta_fb_stars_days']}d to 500" if v["eta_fb_stars_days"] else ""
        lines.append(f"{v['account']:<18} {v['followers']:>6}  {vel}{eta}")
    if r["momentum_anomalies"]:
        lines += ["", "anomalous series:"] + [f"  • {a}" for a in r["momentum_anomalies"]]
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
