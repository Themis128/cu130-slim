"""LinkedIn org-post engagement scoring — Community Management Standard Tier.

Hourly: pulls per-post socialMetadata for the org page's recent posts,
scores engagement-rate vs the rolling 30-day median, and:

- flags >2σ winners as ``boost_candidate`` in ``posts.meta_data`` (picked up
  by boost_winner when Advertising grant lands),
- upserts top commenters into ``engagement_candidates`` so the existing
  auto-approve/CRM pipeline can queue human-gated replies.

Every decision logs ``decision_input`` — the query + values it acted on —
into ``meta_data.decision_input`` (and company_brain when present), per the
data-driven mandate.
"""

import statistics
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

REST = "https://api.linkedin.com/rest"
V2 = "https://api.linkedin.com/v2"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}
LOOKBACK_DAYS = 30
SCORE_WINDOW_DAYS = 30
BOOST_SIGMA = 2.0
MAX_COMMENT_CANDIDATES = 5


def _score(s: dict) -> float:
    """Weighted engagement actions per impression (x100 for readability)."""
    imp = s.get("impressionCount") or s.get("impressions") or 0
    if not imp:
        imp = 1
    actions = (s.get("likeCount") or s.get("reactions") or 0) + 2 * (s.get("commentCount") or 0) + 3 * (s.get("shareCount") or s.get("shareMentions") or 0)
    return actions * 100.0 / imp


async def _social_metadata(client: httpx.AsyncClient, urn: str) -> dict | None:
    """Per-entity socialMetadata — Standard Tier. Falls back to v2."""
    import urllib.parse

    q = urllib.parse.quote(urn, safe="")
    for base in (REST, V2):
        r = await client.get(f"{base}/socialMetadata", params={"entity": q})
        if r.status_code == 200:
            d = r.json()
            el = (d.get("elements") or [d])[0]
            return el if isinstance(el, dict) else None
        if r.status_code == 404:
            return None
    # socialMetadata not on this entity type — try socialActions counters
    r = await client.get(f"{V2}/socialActions/{q}")
    if r.status_code == 200:
        d = r.json()
        return {
            "likeCount": d.get("likesSummary", {}).get("totalLikes") or 0,
            "commentCount": d.get("commentsSummary", {}).get("totalFirstLevelComments") or 0,
            "shareCount": d.get("shareCount") or 0,
            "impressionCount": 0,
        }
    return None


async def _comments(client: httpx.AsyncClient, urn: str) -> list[dict]:
    import urllib.parse

    q = urllib.parse.quote(urn, safe="")
    r = await client.get(f"{REST}/socialActions/{q}/comments", params={"count": 20})
    if r.status_code in (403, 426):
        r = await client.get(f"{V2}/socialActions/{q}/comments", params={"count": 20})
    if r.status_code != 200:
        return []
    return r.json().get("elements") or []


@celery_app.task(name="app.worker.tasks.linkedin_engagement_score.score_linkedin_posts")
def score_linkedin_posts() -> dict:
    return run_async(_score_linkedin_posts())


async def _score_linkedin_posts() -> dict:
    from app.models.content import Post, PostTarget
    from app.models.engagement import EngagementCandidate
    from app.models.social_account import SocialAccount

    async with task_session() as db:
        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == "organization",
                    )
                )
            )
            .scalars()
            .first()
        )
        if not acc or not acc.access_token_enc:
            return {"skip": "no-linkedin-org-account"}
        from app.core.security import decrypt_token

        token = decrypt_token(bytes(acc.access_token_enc))
        cutoff = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
        targets = (
            await db.execute(
                select(PostTarget, Post)
                .join(Post, Post.id == PostTarget.post_id)
                .where(
                    PostTarget.social_account_id == acc.id,
                    PostTarget.status == "published",
                    PostTarget.published_at >= cutoff,
                    PostTarget.platform_post_id.isnot(None),
                )
            )
        ).all()

    headers = {"Authorization": f"Bearer {token}", **HDRS}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        scored: list[tuple[str, str, float, dict]] = []  # post_id, urn, score, stats
        commenters: list[dict] = []
        for tgt, post in targets:
            urn = tgt.platform_post_id
            stats = await _social_metadata(client, urn)
            if stats is None:
                continue
            s = _score(stats)
            scored.append((str(post.id), urn, s, stats))
            for c in (await _comments(client, urn))[:MAX_COMMENT_CANDIDATES]:
                actor = c.get("actor") or ""
                msg = (c.get("message") or {}).get("text") or ""
                commenters.append(
                    {
                        "platform": "linkedin",
                        "author_handle": actor[-118:],
                        "author_name": actor[-118:],
                        "post_id": f"li-comment:{urn}:{actor}"[:118],
                        "post_text": msg[:2000],
                        "posted_at": datetime.now(UTC),
                        "meta_data": {"org_post_urn": urn, "post_db_id": str(post.id), "source": "engagement_score"},
                    }
                )

    if not scored:
        return {"scored": 0, "skip": "no-post-stats"}

    scores = [x[2] for x in scored]
    median = statistics.median(scores) if len(scores) > 1 else scores[0]
    stdev = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    threshold = median + BOOST_SIGMA * stdev if stdev else median * 2
    winners = [x for x in scored if x[2] > threshold and x[2] > 0]

    async with task_session() as db:
        from app.models.content import Post as PostModel

        for post_id, urn, s, stats in winners:
            p = await db.get(PostModel, post_id)
            if p:
                p.meta_data = {
                    **(p.meta_data or {}),
                    "linkedin_stats": stats,
                    "linkedin_score": round(s, 3),
                    "boost_candidate": True,
                    "decision_input": {
                        "task": "linkedin_engagement_score",
                        "score": round(s, 3),
                        "median": round(median, 3),
                        "threshold": round(threshold, 3),
                        "n_posts": len(scores),
                    },
                }
        # upsert commenters (post_id unique → skip dupes)
        added = 0
        for c in commenters:
            exists = (await db.execute(select(EngagementCandidate.id).where(EngagementCandidate.post_id == c["post_id"]))).scalar_one_or_none()
            if not exists:
                db.add(EngagementCandidate(**c))
                added += 1
        await db.commit()

    return {
        "scored": len(scored),
        "median": round(median, 3),
        "threshold": round(threshold, 3),
        "winners": [{"post": w[0], "score": round(w[2], 3)} for w in winners],
        "commenters_added": added,
    }
