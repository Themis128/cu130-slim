"""Brand monitoring service — mentions, sentiment, competitor tracking, health scoring.

Cloudflare-first: uses Cloudflare Workers AI for sentiment analysis,
falls back to Ollama. Uses free APIs (Twitter/X search, Reddit JSON,
Google News RSS) for mention discovery.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand import Brand
from app.models.brand_monitoring import BrandMention, CompetitorSnapshot


async def search_twitter_mentions(
    brand_name: str,
    bearer_token: str | None = None,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Search Twitter/X for brand mentions using the free API tier.

    Returns list of mention dicts with platform, author, content, url, engagement.
    """
    if not bearer_token:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.twitter.com/2/tweets/search/recent",
                params={"query": brand_name, "max_results": max_results},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                {
                    "platform": "twitter",
                    "author": t.get("author_id", ""),
                    "content": t.get("text", ""),
                    "url": f"https://twitter.com/i/web/status/{t.get('id', '')}",
                    "engagement": t.get("public_metrics", {}).get("like_count", 0)
                    + t.get("public_metrics", {}).get("retweet_count", 0),
                    "mentioned_at": t.get("created_at"),
                    "extra_data": t.get("public_metrics", {}),
                }
                for t in data.get("data", [])
            ]
    except Exception:
        return []


async def search_reddit_mentions(brand_name: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Search Reddit for brand mentions using the free JSON API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://www.reddit.com/search.json?q={brand_name}&limit={max_results}&sort=new",
                headers={"User-Agent": "SocialAuto/1.0"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            return [
                {
                    "platform": "reddit",
                    "author": c.get("data", {}).get("author", ""),
                    "content": c.get("data", {}).get("title", "")
                    + " "
                    + c.get("data", {}).get("selftext", "")[:500],
                    "url": f"https://reddit.com{c.get('data', {}).get('permalink', '')}",
                    "engagement": c.get("data", {}).get("score", 0),
                    "mentioned_at": datetime.fromtimestamp(
                        c.get("data", {}).get("created_utc", 0), tz=UTC
                    ).isoformat()
                    if c.get("data", {}).get("created_utc")
                    else None,
                    "extra_data": {"subreddit": c.get("data", {}).get("subreddit", "")},
                }
                for c in children
            ]
    except Exception:
        return []


async def search_google_news_mentions(brand_name: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Search Google News RSS for brand mentions."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://news.google.com/rss/search?q={brand_name}&hl=en-US&gl=US&ceid=US:en",
                headers={"User-Agent": "SocialAuto/1.0"},
            )
            if resp.status_code != 200:
                return []
            # Parse RSS XML
            import xml.etree.ElementTree as ET

            root = ET.fromstring(resp.text)
            items = root.findall(".//item")[:max_results]
            return [
                {
                    "platform": "google_news",
                    "author": item.findtext("source", "Unknown"),
                    "content": item.findtext("title", ""),
                    "url": item.findtext("link", ""),
                    "engagement": 0,
                    "mentioned_at": item.findtext("pubDate"),
                    "extra_data": {},
                }
                for item in items
            ]
    except Exception:
        return []


async def analyze_sentiment(text: str, db: AsyncSession | None = None) -> dict[str, Any]:
    """Analyze sentiment of text using Cloudflare Workers AI (fallback: Ollama).

    Returns dict with sentiment (positive/negative/neutral) and score (-1.0 to 1.0).
    """
    # Try Cloudflare Workers AI first
    try:
        from app.services.inference import call_inference

        result = await call_inference(
            prompt=f'Analyze the sentiment of this text. Return JSON with "sentiment" (positive/negative/neutral) and "score" (-1.0 to 1.0):\n\n"{text[:500]}"',
            provider_name="cloudflare",
            db=db,
            schema={"type": "object", "properties": {
                "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                "score": {"type": "number", "minimum": -1, "maximum": 1},
            }},
        )
        if "response" in result:
            parsed = json.loads(result["response"]) if isinstance(result["response"], str) else result["response"]
            return {"sentiment": parsed.get("sentiment", "neutral"), "score": parsed.get("score", 0.0)}
    except Exception:
        pass

    # Fallback: simple keyword-based sentiment
    positive_words = ["good", "great", "excellent", "amazing", "love", "best", "awesome", "fantastic", "recommend"]
    negative_words = ["bad", "terrible", "awful", "hate", "worst", "horrible", "disappointing", "scam", "poor"]
    text_lower = text.lower()
    pos_count = sum(1 for w in positive_words if w in text_lower)
    neg_count = sum(1 for w in negative_words if w in text_lower)
    if pos_count > neg_count:
        return {"sentiment": "positive", "score": min(0.5 + pos_count * 0.1, 1.0)}
    if neg_count > pos_count:
        return {"sentiment": "negative", "score": max(-0.5 - neg_count * 0.1, -1.0)}
    return {"sentiment": "neutral", "score": 0.0}


async def collect_mentions(
    db: AsyncSession,
    brand: Brand,
    twitter_bearer_token: str | None = None,
) -> list[BrandMention]:
    """Collect mentions from all sources and store them in the database."""
    brand_name = brand.name
    tasks = [
        search_twitter_mentions(brand_name, twitter_bearer_token),
        search_reddit_mentions(brand_name),
        search_google_news_mentions(brand_name),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    mentions: list[BrandMention] = []
    for result in results:
        if isinstance(result, Exception) or not result:
            continue
        for m in result:
            # Analyze sentiment
            sentiment = await analyze_sentiment(m.get("content", ""), db)
            mention = BrandMention(
                brand_id=brand.id,
                platform=m.get("platform", "unknown"),
                author=m.get("author"),
                content=m.get("content", ""),
                url=m.get("url"),
                sentiment=sentiment.get("sentiment"),
                sentiment_score=sentiment.get("score", 0.0),
                engagement=m.get("engagement", 0),
                extra_data=m.get("extra_data", {}),
            )
            if m.get("mentioned_at"):
                try:
                    mention.mentioned_at = datetime.fromisoformat(
                        m["mentioned_at"].replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass
            mentions.append(mention)
            db.add(mention)

    await db.commit()
    return mentions


async def snapshot_competitor(
    db: AsyncSession,
    brand: Brand,
    competitor_name: str,
    platform: str = "twitter",
) -> CompetitorSnapshot | None:
    """Take a snapshot of a competitor's metrics on a given platform."""
    # This would use platform-specific APIs in production
    # For now, we store a placeholder snapshot
    snapshot = CompetitorSnapshot(
        brand_id=brand.id,
        competitor_name=competitor_name,
        platform=platform,
        follower_count=0,
        engagement_rate=0.0,
        post_count=0,
        top_post_content=None,
        top_post_engagement=0,
    )
    db.add(snapshot)
    await db.commit()
    return snapshot


def calculate_health_score(
    mentions: list[BrandMention],
    competitor_snapshots: list[CompetitorSnapshot],
    post_count_30d: int = 0,
    avg_engagement_rate: float = 0.0,
) -> dict[str, Any]:
    """Calculate brand health score from mentions, competitors, and posting metrics.

    Score combines:
    - Reach (mention count + engagement)
    - Sentiment (average sentiment score)
    - Share of voice (vs competitors)
    - Engagement rate
    - Posting consistency

    Returns dict with overall score (0-100) and component scores.
    """
    # Sentiment: average sentiment score mapped to 0-100
    if mentions:
        avg_sentiment = sum(m.sentiment_score or 0 for m in mentions) / len(mentions)
        sentiment_score = (avg_sentiment + 1) / 2 * 100  # -1..1 → 0..100
    else:
        sentiment_score = 50.0

    # Reach: mention count + total engagement
    total_engagement = sum(m.engagement or 0 for m in mentions)
    reach_score = min(len(mentions) * 5 + total_engagement * 0.5, 100)

    # Share of voice: vs competitors
    if competitor_snapshots:
        competitor_engagement = sum(
            s.top_post_engagement or 0 for s in competitor_snapshots
        )
        if total_engagement + competitor_engagement > 0:
            sov_score = total_engagement / (total_engagement + competitor_engagement) * 100
        else:
            sov_score = 50.0
    else:
        sov_score = 50.0

    # Engagement rate
    engagement_score = min(avg_engagement_rate * 10, 100)

    # Posting consistency (at least 1 post per 2 days = 100)
    consistency_score = min(post_count_30d / 15 * 100, 100)

    # Weighted overall score
    overall = (
        sentiment_score * 0.25
        + reach_score * 0.20
        + sov_score * 0.20
        + engagement_score * 0.20
        + consistency_score * 0.15
    )

    return {
        "overall": round(overall, 1),
        "sentiment": round(sentiment_score, 1),
        "reach": round(reach_score, 1),
        "share_of_voice": round(sov_score, 1),
        "engagement": round(engagement_score, 1),
        "consistency": round(consistency_score, 1),
        "mention_count": len(mentions),
        "total_engagement": total_engagement,
        "avg_sentiment": round(avg_sentiment, 2) if mentions else 0,
    }
