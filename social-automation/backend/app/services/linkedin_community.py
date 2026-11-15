"""LinkedIn Community Management — comment moderation + classification.

Uses ``r_organization_social_feed`` / ``w_organization_social_feed`` /
``rw_organization_admin`` (granted on app 264925843) to keep the Company
Page's comment sections clean and to surface buying signals:

- ``classify_comment`` — pure heuristic bucket: ``spam`` | ``question`` |
  ``lead`` | ``praise`` | ``other``. Deliberately conservative: only
  high-confidence spam is auto-actionable; everything ambiguous alerts a
  human instead of acting.
- ``delete_comment`` — ``DELETE /rest/socialActions/{commentUrn}`` hides a
  comment on the org's own post. Gated separately from feed writes via
  ``LINKEDIN_COMMENT_HIDE`` (default off → alert-only mode).
- ``unanswered_comments`` — comments on org posts that have no reply from
  the page actor yet (community-management backlog metric).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import quote

from app.services import linkedin_feed

logger = logging.getLogger(__name__)

COMMENT_HIDE_ENABLED = os.getenv("LINKEDIN_COMMENT_HIDE", "0") == "1"

# Strong spam phrases are unambiguous enough to act on alone. A bare URL is
# NOT — customers legitimately reference their own site — so links only count
# toward spam when corroborated (multiple links or a link shortener).
_SPAM_PATTERNS = [
    re.compile(r"\b(crypto|bitcoin|forex|binary options|casino|viagra|weight loss)\b", re.I),
    re.compile(r"(make money|earn \$|work from home|dm me for)", re.I),
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}.*(buy|sell|promo|discount)", re.I),
]
_URL_RE = re.compile(r"https?://", re.I)
_LINK_SHORTENER_RE = re.compile(r"\b(?:bit\.ly|tinyurl\.com|t\.me|wa\.me|rb\.gy|cutt\.ly|shorturl\w*\.\w+)\b", re.I)
_LEAD_PATTERNS = [
    re.compile(r"\b(price|pricing|cost|quote|demo|trial|interested|how much|proposal)\b", re.I),
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I),
    re.compile(r"(\+?\d[\d\s().-]{7,}\d)"),  # phone-ish
]
_QUESTION_PATTERNS = [re.compile(r"\?"), re.compile(r"\b(how|what|when|where|which|can you|do you)\b", re.I)]
_PRAISE_PATTERNS = [re.compile(r"\b(great|awesome|love|amazing|congrats|well done|impressive|excellent)\b", re.I)]


def classify_comment(text: str) -> str:
    """Bucket a comment: spam | lead | question | praise | other.

    Order matters — spam wins over everything, then buying signals, then
    engagement types. Spam requires a strong phrase or corroborated links;
    a single bare URL falls through so linked questions/leads reach a human.
    """
    t = (text or "").strip()
    if not t:
        return "other"
    strong_spam = any(p.search(t) for p in _SPAM_PATTERNS)
    link_spam = len(_URL_RE.findall(t)) >= 2 or bool(_LINK_SHORTENER_RE.search(t))
    if strong_spam or link_spam:
        return "spam"
    if any(p.search(t) for p in _LEAD_PATTERNS):
        return "lead"
    if any(p.search(t) for p in _QUESTION_PATTERNS):
        return "question"
    if any(p.search(t) for p in _PRAISE_PATTERNS):
        return "praise"
    return "other"


async def org_post_comments(max_posts: int = 10, max_age_h: float = 168.0) -> dict[str, Any]:
    """Comments across the org page's recent posts.

    Returns ``{"comments": [{post_urn, comment_urn, actor, text, created}]}``.
    Read-only; needs ``r_organization_social_feed``.
    """
    ctx = await linkedin_feed.org_ctx()
    if not ctx:
        return {"comments": [], "error": "no-linkedin-account"}
    _, org_urn = ctx
    posts_res = await linkedin_feed.list_posts(org_urn, count=max_posts)
    if posts_res.get("error"):
        return {"comments": [], "error": posts_res["error"], "posts_scanned": 0}
    posts = posts_res.get("posts") or []
    out: list[dict[str, Any]] = []
    for post in posts:
        post_urn = post.get("id") or ""
        if not post_urn.startswith("urn:li:"):
            continue
        age_h = linkedin_engagement_age_h(post)
        if age_h is not None and age_h > max_age_h:
            continue
        cres = await linkedin_feed.list_comments(post_urn, count=50)
        for c in cres.get("comments") or []:
            comment_urn = c.get("commentUrn") or c.get("id") or ""
            # Comment id for DELETE — bare numeric id or last segment of the
            # composite comment URN ``urn:li:comment:(…,id)``.
            cid = str(c.get("id") or "")
            if not cid or ":" in cid:
                cid = comment_urn.rsplit(",", 1)[-1].rstrip(")") if "," in comment_urn else comment_urn.rsplit(":", 1)[-1]
            actor_raw = c.get("actor")
            actor = (actor_raw.get("sub") if isinstance(actor_raw, dict) else actor_raw) or ""
            if isinstance(actor, str) and actor.startswith("urn:li:organization:"):
                continue  # our own replies aren't moderation targets
            message = (((c.get("message") or {}).get("text")) or "")
            out.append(
                {
                    "post_urn": post_urn,
                    "comment_urn": comment_urn,
                    "comment_id": cid,
                    "actor": actor if isinstance(actor, str) else str(actor),
                    "text": message,
                    "created": (c.get("created") or {}).get("time"),
                }
            )
    return {"comments": out, "posts_scanned": len(posts)}


def linkedin_engagement_age_h(post: dict) -> float | None:
    from datetime import UTC, datetime

    ts = post.get("publishedAt") or post.get("createdAt") or post.get("lastModified") or {}
    ms = ts.get("time") if isinstance(ts, dict) else ts
    if not ms:
        return None
    return (datetime.now(UTC).timestamp() * 1000 - float(ms)) / 3_600_000


async def delete_comment(post_urn: str, comment_id: str) -> dict[str, Any]:
    """Delete a comment on the org's own post.

    ``DELETE /rest/socialActions/{shareUrn}/comments/{commentId}`` with
    ``actor={org_urn}`` — verified against MS Learn community-management
    docs. Gated by ``LINKEDIN_COMMENT_HIDE``; without it we report a
    dry-run so callers can alert instead of acting.
    """
    if not COMMENT_HIDE_ENABLED:
        return {"dry_run": True, "post": post_urn, "comment": comment_id}
    ctx = await linkedin_feed.org_ctx()
    if not ctx:
        return {"error": "no-linkedin-account"}
    token, actor_urn = ctx
    import httpx

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(
            f"{linkedin_feed.REST}/socialActions/{quote(post_urn, safe='')}/comments/{quote(comment_id, safe='')}",
            params={"actor": actor_urn},
            headers={"Authorization": f"Bearer {token}", **linkedin_feed.HDRS},
        )
    return {"status": r.status_code, "ok": r.status_code in (200, 204), "error": None if r.status_code in (200, 204) else r.text[:200]}
