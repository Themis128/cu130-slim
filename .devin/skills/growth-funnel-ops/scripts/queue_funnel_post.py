#!/usr/bin/env python3
"""Queue a funnel post through the real quality pipeline.
generate-content -> nlp-check (>=80) -> UTM tag -> create -> schedule.
Run inside social-api:  python /app/skills/queue_funnel_post.py --funnel personal --platform linkedin --topic "..." --cta URL --at HH:MM"""
import argparse
import asyncio
import sys
from zoneinfo import ZoneInfo
sys.path.insert(0, "/app")
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.post import Post
from app.models.user import Team
from app.services.utm import tracked_url

ATH = ZoneInfo("Europe/Athens")
PERSONAL = "https://facebook.com/themis.baltzakis"
BRAND_CTAS = ("https://cloudless.gr/contact", "https://social.cloudless.gr/pricing")

async def main(args):
    async with async_session_maker() as db:
        team = (await db.scalars(select(Team))).first()  # noqa: F841
        # 1-in-3 cadence check for the personal funnel
        if args.funnel == "personal":
            recent = (await db.scalars(
                select(Post).where(Post.status.in_(["published","scheduled"]))
                .order_by(Post.created_at.desc()).limit(20))).all()
            soft = sum(1 for p in recent if "themis.baltzakis" in (p.content_text or ""))
            if recent and soft / len(recent) >= 1/3:
                print(f"SKIP: cadence {soft}/{len(recent)} >= 1/3 — funnel saturated")
                return
        cta = args.cta or (PERSONAL if args.funnel == "personal" else BRAND_CTAS[0])
        slug = f"{args.funnel}-funnel"
        tagged = tracked_url(cta, source=args.platform, medium="organic_social",
                             campaign=slug, content="funnel-post")
        print(f"CTA: {tagged}")
        # TODO: wire generate-content + nlp-check + POST /content/posts + schedule
        # via app.services equivalents (same path POST /api/v1/content/posts takes)
        print("OK: cadence clear — implement create+schedule via app.services.posts")
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--funnel", choices=["personal","brand"], default="personal")
    ap.add_argument("--platform", default="linkedin")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--cta", default=None)
    ap.add_argument("--at", default="20:30")
    asyncio.run(main(ap.parse_args()))
