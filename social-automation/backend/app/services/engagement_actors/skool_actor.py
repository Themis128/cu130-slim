"""Skool community actor — posts via the shared browser bridge.

Manual/assisted only — Skool has no write API. Approval lands the post
through the browser composer; requires a logged-in Skool session in the
shared browser (same session the hourly sync uses).
"""

import logging

logger = logging.getLogger(__name__)


async def act(action, db) -> dict:
    """Post to the Skool community. action.meta_data carries
    {title, body}."""
    from app.services import skool_sync

    meta = action.meta_data or {}
    title = meta.get("title", "")
    body = meta.get("body", "")
    if not body:
        return {"ok": False, "skip": True, "error": "no body"}
    try:
        res = await skool_sync.post_to_community(title, body)
    except Exception as e:
        logger.warning("[skool-actor] post failed: %s", e)
        return {"ok": False, "error": str(e)[:120]}
    if res.get("ok"):
        return {"ok": True}
    # no session/composer = retryable
    return {"ok": False, "skip": True, "error": res.get("error", "?")[:120]}
