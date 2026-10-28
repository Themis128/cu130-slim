#!/usr/bin/env python3
# ruff: noqa: E402
"""Import LinkedIn Campaign Manager report CSVs as ground-truth ad metrics.

Campaign Manager → Export CSV produces UTF-16 TSV files with a 4-line
preamble ("Report Start/End", "Date Generated") and either:

  * a daily metrics table    (campaign / creative / LAN / placements / conv.)
  * stacked segment tables   (demographics — "<X> Segment" header rows)

Rows upsert on their natural grain so re-importing an updated export of the
same window is safe and idempotent.

Usage (inside the social-api container):
    python scripts/import_linkedin_reports.py /path/to/reports
    python scripts/import_linkedin_reports.py report.csv [more.csv ...]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import os
import re
import sys
from datetime import date, datetime
from typing import Any

_CONTAINER = "/app"
_REPO_BACKEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "social-automation", "backend",
)
for path in (_CONTAINER, _REPO_BACKEND):
    if path not in sys.path and os.path.isdir(path):
        sys.path.insert(0, path)

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import async_session_maker
from app.models.linkedin_ads import AdDailyMetric, AdDemographicSegment
from app.models.social_account import SocialAccount
from app.models.user import Team

# filename fragment → report_type (ad_daily_metrics.report_type)
REPORT_TYPES = {
    "campaign_performance_report": "campaign_performance",
    "creative_performance_report": "creative_performance",
    "lan_campaign_performance_report": "lan_campaign_performance",
    "lan_creative_performance_report": "lan_creative_performance",
    "campaign_placement_report": "placements_campaign",
    "creative_placement_report": "placements_creative",
    "conversion_performance_report": "conversion_performance",
    "creative_conversion_performance_report": "creative_conversion_performance",
    "conversation_ads_creative_performance_report": "conversation_ad_performance",
    "demographics_report": "demographics",
}

# "<X> Segment" header → segment_type
SEGMENT_TYPES = {
    "Company Name": "company_name",
    "Company Industry": "company_industry",
    "Company Size": "company_size",
    "Contextual Country/Region": "country",
    "Location": "location",
    "Job Seniority": "job_seniority",
    "Job Title": "job_title",
    "Job Function": "job_function",
    "County": "county",
    "Designated Market Area": "dma",
}

_DATE_HDR = "Start Date (in UTC)"


def _read_rows(path: str) -> list[list[str]]:
    with open(path, "rb") as f:
        head = f.read(4)
    enc = "utf-16" if head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    with open(path, newline="", encoding=enc) as f:
        return list(csv.reader(f, delimiter="\t"))


def _num(v: str) -> float:
    v = (v or "").strip().replace(",", "").replace("%", "").replace("€", "")
    if not v or v == "-":
        return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


def _int(v: str) -> int:
    return int(_num(v))


def _day(v: str) -> date | None:
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", v or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _window(rows: list[list[str]]) -> tuple[date | None, date | None]:
    """Pull 'Report Start/End: <Month D, YYYY, ...>' from the preamble."""
    start = end = None
    for r in rows[:6]:
        cell = (r[0] if r else "").strip().strip('"')
        m = re.match(r"Report (Start|End): (\w+ \d{1,2}, \d{4})", cell)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(2), "%B %d, %Y").date()
        except ValueError:
            continue
        if m.group(1) == "Start":
            start = d
        else:
            end = d
    return start, end


def _report_type(path: str) -> str | None:
    name = os.path.basename(path).lower()
    # Longest fragment first — "lan_campaign_performance_report" contains
    # "campaign_performance_report" as a substring.
    for frag, rt in sorted(REPORT_TYPES.items(), key=lambda kv: -len(kv[0])):
        if frag in name:
            return rt
    return None


def parse_daily(path: str, report_type: str, account_id: str) -> list[dict[str, Any]]:
    rows = _read_rows(path)
    hdr_i = next(
        (i for i, r in enumerate(rows) if r and r[0].strip() == _DATE_HDR), None
    )
    if hdr_i is None:
        return []
    hdr = [h.strip() for h in rows[hdr_i]]
    col = {n: i for i, n in enumerate(hdr)}

    def g(r: list[str], *names: str) -> str:
        for n in names:
            i = col.get(n)
            if i is not None and i < len(r):
                return r[i]
        return ""

    out = []
    for r in rows[hdr_i + 1 :]:
        day = _day(g(r, _DATE_HDR))
        if not day:
            continue  # totals / footer rows
        out.append(
            {
                "platform": "linkedin",
                "account_id": account_id,
                "report_type": report_type,
                "campaign_id": g(r, "Campaign ID").strip(),
                "campaign_name": g(r, "Campaign Name").strip().strip('"'),
                "ad_set_id": g(r, "Ad Set ID").strip(),
                "ad_set_name": g(r, "Ad Set Name").strip().strip('"'),
                "ad_id": g(r, "Ad ID").strip(),
                "ad_name": g(r, "Ad Name").strip().strip('"'),
                # LAN exports split by "Platform" (On LinkedIn vs Audience
                # Network); placements exports use "Placement".
                "placement": g(r, "Placement", "Platform").strip(),
                "status": g(r, "Ad Set Status", "Ad Status").strip().lower(),
                "day": day,
                "impressions": _int(g(r, "Impressions")),
                "clicks": _int(g(r, "Clicks")),
                "engagements": _int(g(r, "Total Engagements")),
                "leads": _int(g(r, "Leads")),
                "conversions": _int(g(r, "Conversions")),
                "reach": _int(g(r, "Reach")),
                "clicks_to_landing_page": _int(g(r, "Clicks to Landing Page")),
                "clicks_to_linkedin_page": _int(g(r, "Clicks to LinkedIn Page")),
                "spend_eur": _num(g(r, "Total Spent")),
                "ctr": _num(g(r, "Click Through Rate")),
                "cpc_eur": _num(g(r, "Average CPC")),
                "cpm_eur": _num(g(r, "Average CPM")),
                "engagement_rate": _num(g(r, "Engagement Rate")),
                "budget_eur": _num(g(r, "Total Budget")),
                "source": "report_csv",
                "raw": {"file": os.path.basename(path)},
            }
        )
    return out


def parse_demographics(path: str, account_id: str) -> list[dict[str, Any]]:
    rows = _read_rows(path)
    win_start, win_end = _window(rows)
    if not (win_start and win_end):
        raise ValueError(f"{path}: missing Report Start/End preamble")
    out = []
    cur_type: str | None = None
    for r in rows:
        first = (r[0] if r else "").strip().strip('"')
        m = re.match(r"(.+) Segment$", first)
        if m:
            cur_type = SEGMENT_TYPES.get(m.group(1).strip())
            continue
        if not first:
            continue  # blank separator between tables
        if cur_type is None:
            continue
        out.append(
            {
                "platform": "linkedin",
                "account_id": account_id,
                "segment_type": cur_type,
                "segment_value": first,
                "window_start": win_start,
                "window_end": win_end,
                "impressions": _int(r[1] if len(r) > 1 else ""),
                "pct_impressions": _num(r[2] if len(r) > 2 else ""),
                "clicks": _int(r[3] if len(r) > 3 else ""),
                "pct_clicks": _num(r[4] if len(r) > 4 else ""),
                "ctr": _num(r[5] if len(r) > 5 else ""),
                "conversions": _int(r[6] if len(r) > 6 else ""),
                "source": "report_csv",
            }
        )
    return out


async def _resolve_team_id() -> Any:
    """Same rule as the daily ads report: the team owning a LinkedIn account,
    else the highest-plan team."""
    from sqlalchemy import case, desc

    async with async_session_maker() as db:
        tid = (
            await db.execute(
                select(SocialAccount.team_id)
                .where(SocialAccount.platform == "linkedin")
                .limit(1)
            )
        ).scalar_one_or_none()
        if tid:
            return tid
        rank = case(
            (Team.plan_tier == "enterprise", 4),
            (Team.plan_tier == "business", 3),
            (Team.plan_tier == "pro", 2),
            (Team.plan_tier == "free", 1),
            else_=0,
        )
        return (
            await db.execute(select(Team.id).order_by(desc(rank)).limit(1))
        ).scalar_one_or_none()


async def import_files(paths: list[str], account_id: str) -> dict[str, Any]:
    team_id = await _resolve_team_id()
    if not team_id:
        return {"ok": False, "error": "no team resolved"}
    stats: dict[str, Any] = {"daily": 0, "demographics": 0, "skipped": [], "files": {}}
    async with async_session_maker() as db:
        for path in paths:
            rt = _report_type(path)
            if rt is None:
                stats["skipped"].append(os.path.basename(path))
                continue
            if rt == "demographics":
                rows = parse_demographics(path, account_id)
                if rows:
                    stmt = pg_insert(AdDemographicSegment).values(
                        [{**r, "team_id": team_id} for r in rows]
                    )
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_ad_demo_segments_grain",
                        set_={
                            c: getattr(stmt.excluded, c)
                            for c in (
                                "impressions", "clicks", "conversions",
                                "ctr", "pct_impressions", "pct_clicks",
                                "captured_at",
                            )
                        },
                    )
                    await db.execute(stmt)
                stats["demographics"] += len(rows)
            else:
                rows = parse_daily(path, rt, account_id)
                if rows:
                    stmt = pg_insert(AdDailyMetric).values(
                        [{**r, "team_id": team_id} for r in rows]
                    )
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_ad_daily_metrics_grain",
                        set_={
                            c: getattr(stmt.excluded, c)
                            for c in (
                                "campaign_name", "ad_set_name", "ad_name",
                                "status", "impressions", "clicks",
                                "engagements", "leads", "conversions", "reach",
                                "clicks_to_landing_page",
                                "clicks_to_linkedin_page",
                                "spend_eur", "ctr", "cpc_eur", "cpm_eur",
                                "engagement_rate", "budget_eur", "captured_at",
                            )
                        },
                    )
                    await db.execute(stmt)
                stats["daily"] += len(rows)
            stats["files"][os.path.basename(path)] = len(rows)
        await db.commit()
    stats["ok"] = True
    stats["team_id"] = str(team_id)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="CSV files or directories")
    ap.add_argument(
        "--account-id",
        default=os.environ.get("LINKEDIN_AD_ACCOUNT_ID", "512642510"),
    )
    args = ap.parse_args()
    files: list[str] = []
    for p in args.paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, "*.csv"))))
        else:
            files.append(p)
    if not files:
        raise SystemExit("no .csv files found")
    result = asyncio.run(import_files(files, args.account_id))
    print(result)


if __name__ == "__main__":
    main()
