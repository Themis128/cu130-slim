"""LinkedIn feed engagement worker — react/comment via Community Management API.

Replaces the browser-sidecar engagement lane for API-covered actions. Reads a
watchlist of author URNs (partner/ecosystem pages), reacts to their fresh posts,
and optionally comments from a template pool — all deduped and rate-capped.

Safety posture:
- Reads always run when a token exists.
- Writes require ``LINKEDIN_FEED_WRITES=1`` (service-level gate) AND are capped
  here: ``LINKEDIN_ENGAGE_MAX_REACTIONS_DAY`` (default 10),
  ``LINKEDIN_ENGAGE_MAX_COMMENTS_DAY`` (default 3), posts older than
  ``LINKEDIN_ENGAGE_MAX_AGE_H`` (default 72h) are skipped.
- Every action is journaled to ``analytics_events`` (event_type
  ``linkedin_engagement``) which doubles as the dedup + daily-cap ledger.
- Comment text comes from ``LINKEDIN_ENGAGE_COMMENT_TEMPLATES`` (``|``-separated)
  or is skipped — no LLM copy in the engagement lane by default.
"""

from __future__ import annotations

import logging
import os
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.models.analytics import AnalyticsEvent
from app.services import linkedin_feed
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

MAX_REACTIONS_DAY = int(os.getenv("LINKEDIN_ENGAGE_MAX_REACTIONS_DAY", "10"))
MAX_COMMENTS_DAY = int(os.getenv("LINKEDIN_ENGAGE_MAX_COMMENTS_DAY", "3"))
MAX_AGE_H = int(os.getenv("LINKEDIN_ENGAGE_MAX_AGE_H", "72"))
POSTS_PER_AUTHOR = int(os.getenv("LINKEDIN_ENGAGE_POSTS_PER_AUTHOR", "5"))
REACTION_TYPES = ["LIKE", "PRAISE", "INTEREST", "APPRECIATION"]


def _watchlist() -> list[str]:
    raw = os.getenv("LINKEDIN_ENGAGE_AUTHORS", "")
    return [u.strip() for u in raw.split(",") if u.strip().startswith("urn:li:")]


def _comment_templates() -> list[str]:
    raw = os.getenv("LINKEDIN_ENGAGE_COMMENT_TEMPLATES", "")
    return [t.strip() for t in raw.split("|") if t.strip()]


def _post_urn(post: dict) -> str | None:
    """Extract the share/activity URN from a /rest/posts element."""
    urn = post.get("id") or ""
    if isinstance(urn, str) and urn.startswith("urn:li:"):
        return urn
    # posts list elements carry 'id' as the post URN; shareUrn may also appear
    for k in ("shareUrn", "activity", "object"):
        v = post.get(k)
        if isinstance(v, str) and v.startswith("urn:li:"):
            return v
    return None


def _post_age_h(post: dict) -> float | None:
    ts = post.get("publishedAt") or post.get("createdAt") or post.get("lastModified") or {}
    if isinstance(ts, dict):
        ms = ts.get("time")
    elif isinstance(ts, int | float):
        ms = ts
    else:
        ms = None
    if not ms:
        return None
    return (datetime.now(UTC).timestamp() * 1000 - float(ms)) / 3_600_000


async def _already_acted(db, team_id, platform_event_id: str) -> bool:
    row = (
        await db.execute(
            select(AnalyticsEvent.id)
            .where(
                AnalyticsEvent.event_type == "linkedin_engagement",
                AnalyticsEvent.platform_event_id == platform_event_id,
                AnalyticsEvent.team_id == team_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def _actions_today(db, team_id) -> tuple[int, int]:
    day_ago = datetime.now(UTC) - timedelta(hours=24)
    rows = (
        await db.execute(
            select(AnalyticsEvent.meta_data)
            .where(
                AnalyticsEvent.event_type == "linkedin_engagement",
                AnalyticsEvent.team_id == team_id,
                AnalyticsEvent.occurred_at >= day_ago,
            )
        )
    ).scalars().all()
    reactions = sum(1 for m in rows if (m or {}).get("action") == "reaction")
    comments = sum(1 for m in rows if (m or {}).get("action") == "comment")
    return reactions, comments


async def _record(db, team_id, account_id, post_urn: str, action: str, detail: dict) -> None:
    db.add(
        AnalyticsEvent(
            team_id=team_id,
            social_account_id=account_id,
            event_type="linkedin_engagement",
            platform="linkedin",
            platform_event_id=post_urn,
            occurred_at=datetime.now(UTC),
            meta_data={"action": action, **detail},
        )
    )
    await db.commit()


async def _run() -> dict:
    authors = _watchlist()
    if not authors:
        return {"skip": "no-watchlist", "hint": "set LINKEDIN_ENGAGE_AUTHORS=urn:li:organization:…,…"}

    from app.models.user import Team

    async with task_session() as db:
        team = (await db.execute(select(Team).limit(1))).scalars().first()
        if not team:
            return {"error": "no-team"}
        team_id = team.id

        # Resolve account ids for journaling
        from app.models.social_account import SocialAccount

        accounts = {
            a.account_type: a.id
            for a in (
                (await db.execute(select(SocialAccount).where(SocialAccount.platform == "linkedin"))).scalars().all()
            )
        }
        member_account_id = accounts.get("person")

        reactions_today, comments_today = await _actions_today(db, team_id)
        templates = _comment_templates()
        stats: dict[str, Any] = {"scanned": 0, "reacted": 0, "commented": 0, "skipped_dedup": 0, "skipped_cap": 0, "skipped_age": 0, "errors": []}

        for author_urn in authors:
            listing = await linkedin_feed.list_posts(author_urn, count=POSTS_PER_AUTHOR)
            posts = listing.get("posts") or []
            if listing.get("status") not in (None, 200):
                stats["errors"].append(f"{author_urn}: list_posts {listing.get('status')}")
                continue
            for post in posts:
                urn = _post_urn(post)
                if not urn:
                    continue
                stats["scanned"] += 1
                age = _post_age_h(post)
                if age is not None and age > MAX_AGE_H:
                    stats["skipped_age"] += 1
                    continue
                if await _already_acted(db, team_id, urn):
                    stats["skipped_dedup"] += 1
                    continue

                # ── reaction ──
                if reactions_today < MAX_REACTIONS_DAY:
                    rtype = random.choice(REACTION_TYPES)
                    res = await linkedin_feed.react("member", urn, rtype)
                    if res.get("ok") or res.get("dry_run"):
                        reactions_today += 1
                        stats["reacted"] += 1
                        await _record(db, team_id, member_account_id, urn, "reaction", {"type": rtype, "result": res})
                    elif res.get("status") in (401, 403):
                        stats["errors"].append(f"react {urn}: HTTP {res.get('status')} (scope?)")
                        break  # scope missing — stop hammering
                    else:
                        stats["errors"].append(f"react {urn}: {res.get('error') or res.get('status')}")
                else:
                    stats["skipped_cap"] += 1

                # ── comment (templates only, smaller cap) ──
                if templates and comments_today < MAX_COMMENTS_DAY and reactions_today <= MAX_REACTIONS_DAY:
                    text = random.choice(templates)
                    cres = await linkedin_feed.comment("member", urn, text)
                    if cres.get("ok") or cres.get("dry_run"):
                        comments_today += 1
                        stats["commented"] += 1
                        await _record(db, team_id, member_account_id, urn, "comment", {"text": text[:120], "result": cres})
                    elif cres.get("status") in (401, 403):
                        stats["errors"].append(f"comment {urn}: HTTP {cres.get('status')} (scope?)")
                    else:
                        stats["errors"].append(f"comment {urn}: {cres.get('error') or cres.get('status')}")

    logger.info("linkedin-engage: %s", stats)
    return stats


@shared_task(name="app.worker.tasks.linkedin_engagement.engage_linkedin_watchlist", bind=True, max_retries=0)
def engage_linkedin_watchlist(self):
    return run_async(_run())
