"""TikTok hashtag ingestion via the dedicated browser sidecar.

Scrapes hashtag feeds (#serverless etc.) for videos; viral-breakout
candidates (>5k views) get flagged for the human gate as duet/stitch
suggestions. Tags from TIKTOK_TARGET_TAGS; hourly :15/:45 sweep.

Uses tiktok-browser-sidecar:9224 (has_session) — the shared engagement
bridge's TikTok session is logged out and returns the login wall.
post_id stores the full `/@user/video/<id>` path: bare `/video/<id>`
URLs 404 on tiktok.com, so the author segment is required.
"""

import asyncio
import json as js
import logging
import os
import re

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)
SIDECAR = os.environ.get("TIKTOK_SIDECAR_URL", "http://tiktok-browser-sidecar:9224")
DEFAULT_TAGS = "serverless,solofounder,ai,smallbusiness,webdev"

EXTRACT_JS = """(() => {
  const out = [];
  document.querySelectorAll('a[href*="/video/"]').forEach(a => {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\\/@([^/\\/]+)\\/video\\/(\\d+)/);
    if (!m) return;
    const card = a.closest('div[class*=DivItemContainer], li') || a.parentElement;
    const txt = ((card && card.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 800);
    const viewsEl = card && card.querySelector('[data-e2e*="views"], [class*=Count], strong');
    const views = ((viewsEl && viewsEl.innerText) || '').trim();
    if (!out.some(o => o.href === href))
      out.push({href, author: m[1], pid: m[2], txt, views});
  });
  return JSON.stringify(out.slice(0, 15));
})()"""


@shared_task(name="app.worker.tasks.engagement_ingest_tt.ingest_tiktok_tags", bind=True, max_retries=0)
def ingest_tiktok_tags(self):
    from app.worker._async import run_async

    return run_async(_run())


def _views(v: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMB]?)", v or "")
    if not m:
        return 0
    n = float(m.group(1))
    return int(n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)])


async def _run():
    import datetime as dt

    from sqlalchemy import select

    from app.models.engagement import EngagementCandidate
    from app.worker._async import task_session

    tags = [t.strip().lstrip("#") for t in os.environ.get("TIKTOK_TARGET_TAGS", DEFAULT_TAGS).split(",") if t.strip()]

    new = 0
    async with httpx.AsyncClient(base_url=SIDECAR, timeout=90) as c:
        try:
            health = (await c.get("/health")).json()
            if not health.get("has_session"):
                return {"skipped": "no-session"}
        except Exception:
            return {"skipped": "sidecar unreachable"}
        async with task_session() as db:
            for tag in tags[:3]:
                try:
                    await c.post("/browse", json={"url": f"https://www.tiktok.com/tag/{tag}"})
                except Exception:
                    continue
                await asyncio.sleep(6)
                r = await c.post("/evaluate", json={"expression": EXTRACT_JS})
                raw = r.json().get("result")
                posts = js.loads(raw or "[]")
                for p in posts:
                    href = p.get("href", "")
                    m = re.search(r"^/@[^/]+/video/\d+$", href)
                    if not m:
                        continue
                    pid = href
                    if (await db.execute(select(EngagementCandidate).where(EngagementCandidate.post_id == pid))).scalars().first():
                        continue
                    views = _views(p.get("views", ""))
                    cand = EngagementCandidate(
                        platform="tiktok",
                        author_handle=(p.get("author") or "")[:120],
                        author_name=(p.get("author") or "")[:120],
                        post_id=pid,
                        post_text=p.get("txt", "")[:1200],
                        posted_at=dt.datetime.now(dt.UTC),
                        is_followed=False,
                        is_connection=False,
                        meta_data={"views": views, "viral": views >= 5000, "source_tag": tag},
                    )
                    db.add(cand)
                    new += 1
            await db.commit()
    logger.info("[tt-ingest] %d new candidates", new)
    return {"new_candidates": new}
