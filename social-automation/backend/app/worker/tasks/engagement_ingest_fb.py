"""Facebook group-feed ingestion via shared browser bridge.

Scrapes whitelisted group feeds for new discussion posts. Groups come
from FACEBOOK_GROUP_URLS (comma-separated full URLs); empty = skip.
Hourly :00/:30 sweep. Meta caps: max 5 comments/hour enforced at queue.
"""
import asyncio
import json as js
import logging
import os
import re

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)
BRIDGE = os.environ.get("FB_BROWSER_URL", "http://browser-novnc:9223")

EXTRACT_JS = """(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  // wait for real hydrated articles (FB lazy-loads; skeleton divs have no text)
  const realArts = () => [...document.querySelectorAll('div[role=article]')]
    .filter(el => (el.innerText || '').trim().length > 40);
  for (let w = 0; w < 15 && realArts().length === 0; w++) {
    window.scrollBy(0, 900);
    await sleep(2000);
  }
  const out = [];
  for (let s = 0; s < 5; s++) {
    realArts().forEach(el => {
      const link = el.querySelector('a[href*="/posts/"], a[href*="permalink"], a[href*="story_fbid"], a[href*="multi_permalinks"], a[href*="__cft__"]');
      const href = link ? link.getAttribute('href') : '';
      const author = el.querySelector('h3 a, strong a, a[role=link]')?.innerText?.trim() || '';
      const txt = (el.innerText || '').trim().slice(0, 1200);
      if (href && !out.some(o => o.href === href))
        out.push({href: href.slice(0, 200), author, txt});
    });
    window.scrollBy(0, 1500);
    await sleep(2200);
  }
  return JSON.stringify(out.slice(0, 10));
})()"""


@shared_task(name="app.worker.tasks.engagement_ingest_fb.ingest_fb_groups",
             bind=True, max_retries=0)
def ingest_fb_groups(self):
    from app.worker._async import run_async
    return run_async(_run())


async def _ev(c, expr):
    r = await c.post("/session/evaluate", json={"expression": expr})
    return r.json().get("result")


async def _run():
    import datetime as dt

    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.engagement import EngagementCandidate

    groups = [g.strip() for g in
              os.environ.get("FACEBOOK_GROUP_URLS", "").split(",") if g.strip()]
    if not groups:
        return {"skipped": "no FACEBOOK_GROUP_URLS configured"}

    new = 0
    async with httpx.AsyncClient(base_url=BRIDGE, timeout=90) as c:
        try:
            await c.post("/session/start",
                         json={"platform": "facebook", "force": True})
        except Exception:
            return {"skipped": "browser busy"}
        async with async_session_maker() as db:
            for gurl in groups[:3]:  # cap: 3 groups per sweep
                await c.post("/session/navigate",
                             json={"url": gurl + ("&" if "?" in gurl else "?") +
                                    "sorting_setting=CHRONOLOGICAL"})
                await asyncio.sleep(8)
                raw = await _ev(c, EXTRACT_JS)
                posts = js.loads(raw or "[]")
                for p in posts:
                    href = p.get("href", "")
                    m = re.search(r"(?:posts/|permalink/|story_fbid=)([\w.%&=-]+)", href)
                    pid = m.group(1) if m else href[-60:]
                    if not pid:
                        continue
                    if (await db.execute(select(EngagementCandidate).where(
                            EngagementCandidate.post_id == pid))).scalars().first():
                        continue
                    db.add(EngagementCandidate(
                        platform="facebook",
                        author_handle=p.get("author", "")[:120],
                        author_name=p.get("author", "")[:120],
                        post_id=pid[:120],
                        post_text=p.get("txt", "")[:1500],
                        posted_at=dt.datetime.now(dt.UTC),
                        is_connection=True, is_followed=True))
                    new += 1
            await db.commit()
    logger.info("[fb-ingest] %d new candidates from %d groups", new, len(groups))
    return {"new_candidates": new, "groups": len(groups)}
