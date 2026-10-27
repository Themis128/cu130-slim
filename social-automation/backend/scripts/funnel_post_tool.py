#!/usr/bin/env python3
"""Queue a funnel post through the real executor pipeline.

Cadence check (1-in-3 personal funnel) -> builds a routable action line ->
strategy_report.execute_report_actions (copy -> SEO>=90 -> media ->
compliance -> schedule, same chain the daily brief uses).

Run inside social-api:
  python /app/scripts/funnel_post_tool.py --funnel personal \
      --platform LinkedIn --topic "200s->10ms session polls build-log" --at 20:30
"""
import argparse, asyncio, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, "/app")
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.content import Post
from app.models.user import Team
from app.services.utm import tracked_url
from app.services.strategy_report import StrategyReport, execute_report_actions

ATH = ZoneInfo("Europe/Athens")
PERSONAL = "https://cloudless.gr/links"
BRAND_CTAS = ("https://cloudless.gr/contact", "https://social.cloudless.gr/pricing")

async def main(a):
    async with async_session_maker() as db:
        team = (await db.scalars(select(Team))).first()
        if not team:
            print("FAIL: no team"); return
        if a.funnel == "personal":
            recent = (await db.scalars(select(Post).where(
                Post.status.in_(["published", "scheduled"]))
                .order_by(Post.created_at.desc()).limit(20))).all()
            soft = sum(1 for p in recent if "themis.baltzakis" in (p.content_text or "") or "cloudless.gr/links" in (p.content_text or ""))
            if recent and soft / len(recent) >= 1 / 3:
                print(f"SKIP: cadence {soft}/{len(recent)} >= 1/3 — funnel saturated"); return
        cta = a.cta or (PERSONAL if a.funnel == "personal" else BRAND_CTAS[0])
        tagged = tracked_url(cta, source=a.platform.lower(), medium="organic_social",
                             campaign=f"{a.funnel}-funnel", content="funnel-post")
        now = datetime.now(ATH)
        target = now.date() if a.at > (now + timedelta(hours=2)).strftime("%H:%M") \
            else (now + timedelta(days=1)).date()
        line = (f"1. {a.platform} - {a.at} - text - {a.topic}. "
                f"Soft CTA: point readers to {tagged}")
        rep = StrategyReport(generated_at=datetime.now(ATH), timezone="Europe/Athens",
                             team_name=team.name)
        rep.actions = [line]
        out = await execute_report_actions(db, team, rep)
        print("RESULT:", out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--funnel", choices=["personal", "brand"], default="personal")
    ap.add_argument("--platform", default="LinkedIn")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--cta", default=None)
    ap.add_argument("--at", default="20:30")
    asyncio.run(main(ap.parse_args()))
