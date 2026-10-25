"""TikTok hashtag ingestion via shared browser bridge.

Scrapes hashtag feeds (#serverless etc.) for videos; viral-breakout
candidates (>5k views) get flagged for the human gate as duet/stitch
suggestions. Tags from TIKTOK_TARGET_TAGS; hourly :15/:45 sweep.
"""
import asyncio
import json as js
import logging
import os
import re

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)
BRIDGE = "http://browser-novnc:9223"
DEFAULT_TAGS = "serverless,solofounder,ai,smallbusiness,webdev"

EXTRACT_JS = """(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const out = [];
  for (let s = 0; s < 3; s++) {
    document.querySelectorAll('div[data-e2e*=challenge-item], div[class*=DivItemContainerV2]').forEach(el => {
      const link = el.querySelector('a[href*="/video/"]');
      const href = link ? link.getAttribute('href') : '';
      const author = el.querySelector('[data-e2e*=author], a[href^="/@"]')?.innerText?.trim() || '';
      const txt = (el.querySelector('[data-e2e*=desc], span')?.innerText || '').trim().slice(0, 800);
      const views = el.querySelector('[data-e2e*=views], strong')?.innerText?.trim() || '';
      if (href && !out.some(o => o.href === href))
        out.push({href, author, txt, views});
    });
    window.scrollBy(0, 1400);
    await sleep(1500);
  }
  return JSON.stringify(out.slice(0, 12));
})()"""


@shared_task(name="app.worker.tasks.engagement_ingest_tt.ingest_tiktok_tags",
             bind=True, max_retries=0)
def ingest_tiktok_tags(self):
    from app.worker._async import run_async
    return run_async(_run())


def _views(v: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMB]?)", v or "")
    if not m:
        return 0
    n = float(m.group(1))
    return int(n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)])


async def _ev(c, expr):
    r = await c.post("/session/evaluate", json={"expression": expr})
    return r.json().get("result")


async def _run():
    import datetime as dt

    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.engagement import EngagementCandidate

    tags = [t.strip().lstrip("#") for t in
            os.environ.get("TIKTOK_TARGET_TAGS", DEFAULT_TAGS).split(",")
            if t.strip()]

    new = 0
    async with httpx.AsyncClient(base_url=BRIDGE, timeout=90) as c:
        try:
            await c.post("/session/start",
                         json={"platform": "tiktok", "force": True})
        except Exception:
            return {"skipped": "browser busy"}
        async with async_session_maker() as db:
            for tag in tags[:3]:
                await c.post("/session/navigate",
                             json={"url": f"https://www.tiktok.com/tag/{tag}"})
                await asyncio.sleep(8)
                raw = await _ev(c, EXTRACT_JS)
                posts = js.loads(raw or "[]")
                for p in posts:
                    href = p.get("href", "")
                    m = re.search(r"/video/(\d+)", href)
                    pid = m.group(1) if m else ""
                    if not pid:
                        continue
                    if (await db.execute(select(EngagementCandidate).where(
                            EngagementCandidate.post_id == pid))).scalars().first():
                        continue
                    views = _views(p.get("views", ""))
                    cand = EngagementCandidate(
                        platform="tiktok",
                        author_handle=p.get("author", "")[:120],
                        author_name=p.get("author", "")[:120],
                        post_id=pid,
                        post_text=p.get("txt", "")[:1200],
                        posted_at=dt.datetime.now(dt.UTC),
                        is_followed=False, is_connection=False,
                        meta_data={"views": views,
                                   "viral": views >= 5000,
                                   "source_tag": tag})
                    db.add(cand)
                    new += 1
            await db.commit()
    logger.info("[tt-ingest] %d new candidates", new)
    return {"new_candidates": new}
