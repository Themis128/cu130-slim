"""LinkedIn community-management beat — comment moderation + signal alerts.

Every ``LINKEDIN_COMMUNITY_INTERVAL_MIN`` (default 120m) the task:

1. Pulls comments on the Company Page's recent posts
   (``r_organization_social_feed``).
2. Dedupes against ``analytics_events`` (``platform_event_id`` =
   ``comment:{comment_urn}``) so each comment is processed once.
3. Classifies each new comment:
   - ``spam`` → ``linkedin_community.delete_comment`` — itself gated by
     ``LINKEDIN_COMMENT_HIDE`` (default off → dry-run + alert so a human
     can enable deletion deliberately).
   - ``lead`` / ``question`` → P2 Telegram alert with post + text —
     these are buying signals and unanswered questions on our posts.
   - ``praise`` / ``other`` → journaled only.
4. Reports the unanswered-comment backlog count in the run stats.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.models.analytics import AnalyticsEvent
from app.services import linkedin_community
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

MAX_POSTS = int(os.getenv("LINKEDIN_COMMUNITY_MAX_POSTS", "10"))
MAX_AGE_H = float(os.getenv("LINKEDIN_COMMUNITY_MAX_AGE_H", "168"))
ALERT_MIN_LEAD = int(os.getenv("LINKEDIN_COMMUNITY_ALERT_MIN_LEAD", "1"))


@shared_task(
    name="app.worker.tasks.linkedin_community.moderate_linkedin_community",
    bind=True,
    max_retries=0,
)
def moderate_linkedin_community(self):
    return run_async(_run())


async def _already_seen(db, team_id, comment_urn: str) -> bool:
    row = (
        await db.execute(
            select(AnalyticsEvent.id)
            .where(
                AnalyticsEvent.event_type == "linkedin_community_comment",
                AnalyticsEvent.platform_event_id == comment_urn,
                AnalyticsEvent.team_id == team_id,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _record(db, team_id, account_id, comment: dict, bucket: str, action: str) -> None:
    db.add(
        AnalyticsEvent(
            team_id=team_id,
            social_account_id=account_id,
            event_type="linkedin_community_comment",
            platform="linkedin",
            platform_event_id=comment["comment_urn"],
            occurred_at=datetime.now(UTC),
            meta_data={
                "bucket": bucket,
                "action": action,
                "post_urn": comment.get("post_urn"),
                "actor": comment.get("actor"),
                "text": (comment.get("text") or "")[:300],
            },
        )
    )


async def _run() -> dict[str, Any]:
    res = await linkedin_community.org_post_comments(max_posts=MAX_POSTS, max_age_h=MAX_AGE_H)
    comments = res.get("comments") or []
    if res.get("error"):
        return {"error": res["error"], "comments": 0}

    stats: dict[str, Any] = {
        "comments": len(comments),
        "posts_scanned": res.get("posts_scanned", 0),
        "spam": 0,
        "deleted": 0,
        "delete_dry_run": 0,
        "leads": 0,
        "questions": 0,
        "dedup": 0,
        "errors": [],
    }
    alerts: list[str] = []

    async with task_session() as db:
        from app.models.social_account import SocialAccount

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
        if not acc:
            return {"error": "no-linkedin-org-account"}
        team_id, account_id = acc.team_id, acc.id

        for c in comments:
            urn = c.get("comment_urn")
            if not urn or await _already_seen(db, team_id, urn):
                stats["dedup"] += 1
                continue
            bucket = linkedin_community.classify_comment(c.get("text") or "")
            action = "journal"
            if bucket == "spam":
                stats["spam"] += 1
                d = await linkedin_community.delete_comment(c["post_urn"], c.get("comment_id") or "")
                action = "delete"
                if d.get("dry_run"):
                    stats["delete_dry_run"] += 1
                    action = "delete_dry_run"
                    alerts.append(f"spam comment (hide disabled): {(c.get('text') or '')[:120]}")
                elif d.get("ok"):
                    stats["deleted"] += 1
                else:
                    stats["errors"].append(f"delete {urn}: {d.get('status')} {d.get('error')}")
            elif bucket in ("lead", "question"):
                stats["leads" if bucket == "lead" else "questions"] += 1
                alerts.append(f"{bucket}: {(c.get('text') or '')[:160]}")
            await _record(db, team_id, account_id, c, bucket, action)
        await db.commit()

    if alerts:
        from app.services.slack_notifications import send_telegram_alert

        body = "\n".join(f"• {a}" for a in alerts[:10])
        await send_telegram_alert(
            "ops",
            f"LinkedIn community: {stats['leads']} lead(s), {stats['questions']} question(s), "
            f"{stats['spam']} spam ({stats['deleted']} deleted) on Company Page comments\n{body}",
        )
    logger.info("linkedin-community: %s", stats)
    return stats
