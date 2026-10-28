"""LinkedIn engagement actor — like/comment via the browser sidecar.

Personal engagement has no public API — actions run through the logged-in
browser session via voyager fetch() inside the page context.
"""

import json
import logging

import httpx

logger = logging.getLogger(__name__)

SIDECAR = "http://linkedin-browser-sidecar:9225"

LIKE_JS = """return await (async () => {
  const res = await fetch('/voyager/api/voyagerSocialDashReactions?threadKey=urn%3Ali%3AugcPost%3A{PID}', {
    method: 'POST', credentials: 'include',
    headers: {'csrf-token': document.cookie.match(/JSESSIONID="?([^";]+)/)?.[1] || '',
              'Content-Type': 'application/json'},
    body: JSON.stringify({reactionType: 'LIKE'})
  });
  return res.status;
})()"""

COMMENT_JS = """return await (async () => {
  const res = await fetch('/voyager/api/voyagerSocialDashComments?decorationId=com.linkedin.voyager.dash.deco.social.SocialComment-26', {
    method: 'POST', credentials: 'include',
    headers: {'csrf-token': document.cookie.match(/JSESSIONID="?([^";]+)/)?.[1] || '',
              'Content-Type': 'application/json'},
    body: JSON.stringify({commentary: {text: {text: {TEXT}}}, threadUrn: '{URN}'})
  });
  return res.status;
})()"""


async def _health(c):
    return (await c.get("/health")).json()


async def act(action, db) -> dict:
    async with httpx.AsyncClient(base_url=SIDECAR, timeout=60) as c:
        h = await _health(c)
        if h.get("rate_limited"):
            return {"error": "rate_limited", "skip": True}
        if not h.get("has_session"):
            return {"error": "no_session"}

        if action.action == "like":
            pid = action.target_post_id.split(":")[-1]
            r = await c.post("/debug/eval", json={"expr": LIKE_JS.replace("{PID}", pid)})
        elif action.action == "comment":
            if not action.comment_draft:
                return {"error": "empty comment draft"}
            expr = COMMENT_JS.replace("{TEXT}", json.dumps(action.comment_draft)).replace("{URN}", action.target_post_id)
            r = await c.post("/debug/eval", json={"expr": expr})
        else:  # repost — needs UI flow, defer
            return {"error": "repost not implemented for linkedin", "skip": True}

        status = r.json().get("result")
        if str(status).startswith("2"):
            return {"ok": True, "resp": {"voyager_status": status}}
        logger.warning("[li-actor] %s failed: %s", action.action, r.text[:200])
        return {"error": f"voyager {status}"}
