import os
"""Instagram engagement actor — like via in-page UI click on the feed card.

Finds the article whose link matches the target shortcode and clicks its
Like button (aria-label). Comments stay human-gated and are not auto-acted.
Skips cleanly when the shared browser is busy.
"""
import logging

import httpx

logger = logging.getLogger(__name__)

BRIDGE = os.environ.get("ENGAGEMENT_BROWSER_URL", "http://engagement-browser:9223")

LIKE_JS = """(() => {
  const el = [...document.querySelectorAll('article')].find(a =>
    a.querySelector('a[href*="/p/{SC}"], a[href*="/reel/{SC}"]'));
  if (!el) return 'post-not-visible';
  const btn = el.querySelector('[aria-label="Like"]');
  if (!btn) return 'already-liked-or-no-btn';
  (btn.closest('button') || btn).click();
  return 'liked';
})()"""


async def act(action, db) -> dict:
    if action.action != "like":
        return {"error": "ig: only like auto-acted", "skip": True}
    sc = action.target_post_id
    async with httpx.AsyncClient(base_url=BRIDGE, timeout=60) as c:
        try:
            await c.post("/session/start",
                         json={"platform": "instagram", "force": True})
        except Exception:
            return {"error": "browser busy", "skip": True}
        r = await c.post("/session/evaluate", json={
            "expression": LIKE_JS.replace("{SC}", sc)})
        res = r.json().get("result", "")
    if res == "liked":
        return {"ok": True}
    if res == "already-liked-or-no-btn":
        return {"ok": True, "resp": {"note": res}}
    if not res or res == "post-not-visible":
        # empty eval = browser stolen mid-call; post off-screen = retry next pass
        return {"error": res or "browser busy", "skip": True}
    logger.warning("[ig-actor] like %s: %s", sc, res)
    return {"error": res}
