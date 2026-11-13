"""LinkedIn engagement actor — comment via official API, like via sidecar.

Comments go through the Community Management API first (``w_member_social``
grant on app 264925843) — the sanctioned lane that keeps working while
browser-touching lanes are paused. Likes have no member API equivalent and
still run through the logged-in browser session via voyager fetch().
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


async def _api_comment(action, db) -> dict | None:
    """Post the comment through the official API.

    Returns:
      - ``{"ok": True, ...}`` on success
      - ``{"error": ..., "skip": True}`` on a definitive API refusal —
        the platform answered, so retrying via the browser sidecar would
        risk a duplicate or violate the LinkedIn cooldown policy
      - ``None`` on infrastructure failure (no account/token, timeout,
        decrypt error) so the caller may fall back to the sidecar
    """
    try:
        from sqlalchemy import select

        from app.core.security import decrypt_token
        from app.models.social_account import SocialAccount
        from app.services.linkedin_api import LinkedInAPIClient

        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == "person",
                        SocialAccount.status == "active",
                    )
                )
            )
            .scalars()
            .first()
        )
        if not acc or not acc.access_token_enc:
            return None
        token = decrypt_token(bytes(acc.access_token_enc))
        meta = acc.meta_data or {}
        actor_urn = str(meta.get("author_urn") or f"urn:li:person:{acc.account_id}")
        client = LinkedInAPIClient(access_token=token)
        res = await client.create_comment(
            post_urn=action.target_post_id,
            text=action.comment_draft,
            creator_urn=actor_urn,
        )
        if res.success:
            return {"ok": True, "resp": {"via": "official-api", "comment_id": res.platform_post_id}}
        logger.warning("[li-actor] api comment refused: %s", res.error)
        return {"error": f"api_refused: {res.error}", "skip": True}
    except Exception as exc:  # noqa: BLE001 — fall back to sidecar
        logger.warning("[li-actor] api comment path error: %s", exc)
        return None


async def act(action, db) -> dict:
    if action.action == "comment" and action.comment_draft:
        api_res = await _api_comment(action, db)
        if api_res is not None:
            return api_res
        # api_res None → infra failure only; API refusals return early above

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
