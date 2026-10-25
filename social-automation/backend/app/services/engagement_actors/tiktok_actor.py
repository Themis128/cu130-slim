import os
"""TikTok engagement actor — punchy short comments via in-page UI.

Comment drafts for TikTok get rewritten short (<10 words) by the 3C
variant prompt upstream. Viral candidates route to the gate as
duet/stitch suggestions (manual phone action — never automated).
"""
import asyncio
import json
import logging

import httpx

logger = logging.getLogger(__name__)
BRIDGE = os.environ.get("ENGAGEMENT_BROWSER_URL", "http://engagement-browser:9223")


async def act(action, db) -> dict:
    if action.action in ("repost", "duet_flag"):
        return {"error": "tiktok: duet/stitch are manual — gate flagged", "skip": True}
    async with httpx.AsyncClient(base_url=BRIDGE, timeout=90) as c:
        try:
            await c.post("/session/start",
                         json={"platform": "tiktok", "force": True})
        except Exception:
            return {"error": "browser busy", "skip": True}
        await c.post("/session/navigate",
                     json={"url": f"https://www.tiktok.com/video/{action.target_post_id}"})
        await asyncio.sleep(6)
        if action.action == "like":
            expr = ("(() => {const b=document.querySelector("
                    "'[data-e2e=like-icon],[data-e2e*=like] button');"
                    "if(!b)return 'no-btn';b.click();return 'liked'})()")
            r = await c.post("/session/evaluate", json={"expression": expr})
            res = r.json().get("result", "")
            return {"ok": True} if res == "liked" else {"error": res, "skip": True}
        if action.action == "comment":
            if not action.comment_draft:
                return {"error": "empty draft"}
            expr = f"""(async () => {{
              const box = document.querySelector('div[contenteditable=true][data-e2e*=comment], div[contenteditable=true]');
              if (!box) return 'no-comment-box';
              box.focus();
              document.execCommand('insertText', false, {json.dumps(action.comment_draft)});
              const btn = [...document.querySelectorAll('[data-e2e*=comment-post],[role=button]')]
                .find(b => /post|comment/i.test(b.innerText||b.getAttribute('aria-label')||''));
              if (!btn) return 'no-submit';
              btn.click();
              return 'commented';
            }})()"""
            r = await c.post("/session/evaluate", json={"expression": expr})
            res = r.json().get("result", "")
            if res == "commented":
                return {"ok": True}
            return {"error": res or "eval failed", "skip": res == "no-comment-box"}
        return {"error": f"tt: {action.action} unsupported", "skip": True}
