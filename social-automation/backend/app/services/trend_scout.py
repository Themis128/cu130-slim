"""Trend scout service — discovers trending topics for content generation.

Uses free APIs: Twitter/X trends (if bearer token available), Reddit hot posts,
and recent top-performing posts from the user's own analytics.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import PostAnalyticsSnapshot
from app.models.content import Post, PostStatus


async def get_twitter_trends(
    bearer_token: str | None = None,
    woeid: int = 1,  # 1 = worldwide
) -> list[dict[str, Any]]:
    """Get trending topics from Twitter/X.

    Requires a bearer token with tweet.read scope.
    Returns list of {name, url, tweet_volume}.
    """
    if not bearer_token:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.twitter.com/2/trends/by/woeid/{woeid}",
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                {
                    "name": t.get("name", ""),
                    "url": t.get("url", ""),
                    "tweet_volume": t.get("tweet_volume", 0),
                }
                for t in data.get("trends", [])
            ]
    except Exception:
        return []


async def get_reddit_hot(subreddit: str = "marketing", limit: int = 10) -> list[dict[str, Any]]:
    """Get hot posts from a subreddit using the free JSON API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}",
                headers={"User-Agent": "SocialAuto/1.0"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            return [
                {
                    "title": c.get("data", {}).get("title", ""),
                    "url": f"https://reddit.com{c.get('data', {}).get('permalink', '')}",
                    "score": c.get("data", {}).get("score", 0),
                    "num_comments": c.get("data", {}).get("num_comments", 0),
                    "subreddit": c.get("data", {}).get("subreddit", ""),
                }
                for c in children
            ]
    except Exception:
        return []


async def get_top_performing_posts(
    db: AsyncSession,
    team_id: Any,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Get the user's own top-performing posts from analytics."""
    try:
        result = await db.execute(
            select(Post, PostAnalyticsSnapshot)
            .join(PostAnalyticsSnapshot, PostAnalyticsSnapshot.post_id == Post.id)
            .where(Post.team_id == team_id, Post.status == PostStatus.PUBLISHED)
            .order_by(PostAnalyticsSnapshot.impressions.desc())
            .limit(limit)
        )
        rows = result.all()
        return [
            {
                "title": post.content[:100] if post.content else "",
                "impressions": analytics.impressions or 0,
                "engagement": (analytics.likes or 0) + (analytics.comments or 0) + (analytics.shares or 0),
                "platform": analytics.platform if hasattr(analytics, "platform") else "unknown",
            }
            for post, analytics in rows
        ]
    except Exception:
        return []


async def scout_trends(
    db: AsyncSession,
    team_id: Any,
    twitter_bearer_token: str | None = None,
    subreddits: list[str] | None = None,
) -> dict[str, Any]:
    """Collect trends from all sources.

    Returns dict with twitter_trends, reddit_hot, top_posts.
    """
    import asyncio

    subreddits = subreddits or ["marketing", "socialmedia", "contentmarketing"]
    reddit_tasks = [get_reddit_hot(sub) for sub in subreddits]

    twitter_task = get_twitter_trends(twitter_bearer_token)
    top_posts_task = get_top_performing_posts(db, team_id)

    twitter_result, reddit_results, top_posts = await asyncio.gather(
        twitter_task,
        asyncio.gather(*reddit_tasks, return_exceptions=True),
        top_posts_task,
    )

    reddit_hot: list[dict[str, Any]] = []
    for r in reddit_results:
        if isinstance(r, list):
            reddit_hot.extend(r)

    return {
        "twitter_trends": twitter_result,
        "reddit_hot": reddit_hot,
        "top_posts": top_posts,
    }
