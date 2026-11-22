"""Instagram publish-lane guard — auto-failover fb_onboarding → business_login.

Incident 2026-11-22: the fb_onboarding IG row kept ``status=active`` while
every ``graph.facebook.com/{ig-user}/media`` call returned OAuthException
#10 — the Page lost ``instagram_business_account`` (known unresolved Meta
Graph bug; re-auth does not restore it). The instagram2/business_login row
(graph.instagram.com, instagram_business_* scopes) does not need the Page
link and published the same post cleanly.

This sweep (every 15 min) closes the gap automatically:

1. Probe each team's ``business_login`` IG row token live
   (``graph.instagram.com/v21.0/me``).
2. Find ``post_targets`` on OTHER instagram rows (typically
   ``fb_onboarding``) failed within the window whose error matches the
   permission-denied signature (#10 / instagram_content_publish /
   "does not have permission").
3. Repoint those targets + their queue rows to the healthy
   ``business_login`` account (status → pending, attempts reset) so the
   meta worker publishes via the working lane on the next beat.
4. Alert Slack when it acts, and when a business_login token itself is
   dead (needs the instagram2 OAuth re-drive — see
   ig-business-login-ops runbook / instagram_reauth lane).

Content-terminal IG failures (media rules, caption limits) are NOT
touched — only the permission-denied class.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
from celery import shared_task
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.content import PostTarget
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

_FAIL_WINDOW = timedelta(days=7)
_GRAPH_ME = "https://graph.instagram.com/v21.0/me"

# Meta permission-denied signatures seen on the broken Page-link lane.
_PERMISSION_MARKERS = (
    "#10",
    "instagram_content_publish",
    "does not have permission for this action",
    "requires instagram_content_publish",
)


async def _business_login_rows(db, team_id) -> list[SocialAccount]:
    rows = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "instagram",
                SocialAccount.team_id == team_id,
                SocialAccount.meta_data["login_type"].astext == "business_login",
            )
        )
    ).scalars().all()
    return rows


async def _token_alive(account: SocialAccount) -> bool:
    try:
        token = decrypt_token(bytes(account.access_token_enc))
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                _GRAPH_ME, params={"fields": "user_id,username", "access_token": token}
            )
        return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("ig lane guard token probe failed for %s: %s", account.id, exc)
        return False


def _is_permission_failure(err: str | None) -> bool:
    low = (err or "").lower()
    return any(m in low for m in _PERMISSION_MARKERS)


async def _sweep() -> dict:
    now = datetime.now(UTC)
    repointed = 0
    dead_tokens: list[str] = []
    async with task_session() as db:
        ig_accounts = (
            (
                await db.execute(
                    select(SocialAccount).where(SocialAccount.platform == "instagram")
                )
            )
            .scalars()
            .all()
        )
        by_team: dict = {}
        for acc in ig_accounts:
            by_team.setdefault(acc.team_id, []).append(acc)

        for team_id, accounts in by_team.items():
            healthy_bl = [
                a for a in await _business_login_rows(db, team_id)
                if a.status == "active" and await _token_alive(a)
            ]
            bl_ids = {a.id for a in accounts if (a.meta_data or {}).get("login_type") == "business_login"}
            dead_bl = [
                a.username or str(a.id)
                for a in accounts
                if a.id in bl_ids and a.status == "active" and a not in healthy_bl
            ]
            dead_tokens.extend(dead_bl)
            if not healthy_bl:
                continue
            target_acc = healthy_bl[0]

            # Failed IG targets on non-business_login rows with permission errors
            failed = (
                (
                    await db.execute(
                        select(PostTarget).where(
                            PostTarget.social_account_id.in_(
                                [a.id for a in accounts if a.id not in bl_ids]
                            ),
                            PostTarget.status == "failed",
                            PostTarget.updated_at > now - _FAIL_WINDOW,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for tgt in failed:
                if not _is_permission_failure(tgt.error_message):
                    continue
                # Guard the unique active-target constraint: if the
                # business_login row already has a live queue row or
                # target for this post, just retire the broken one.
                active_q = (
                    await db.execute(
                        select(PublishQueue.id).where(
                            PublishQueue.post_id == tgt.post_id,
                            PublishQueue.social_account_id == target_acc.id,
                            PublishQueue.status.in_(
                                [QueueStatus.PENDING, QueueStatus.PROCESSING]
                            ),
                        )
                    )
                ).scalar_one_or_none()
                bl_target = (
                    await db.execute(
                        select(PostTarget).where(
                            PostTarget.post_id == tgt.post_id,
                            PostTarget.social_account_id == target_acc.id,
                        )
                    )
                ).scalar_one_or_none()
                if active_q is None and bl_target is None:
                    # repoint queue rows (any non-completed status)
                    await db.execute(
                        PublishQueue.__table__.update()
                        .where(
                            PublishQueue.post_id == tgt.post_id,
                            PublishQueue.social_account_id == tgt.social_account_id,
                            PublishQueue.status.in_(
                                [QueueStatus.PENDING, QueueStatus.PROCESSING, QueueStatus.FAILED]
                            ),
                        )
                        .values(
                            social_account_id=target_acc.id,
                            status=QueueStatus.PENDING,
                            attempts=0,
                            locked_at=None,
                            locked_by=None,
                            scheduled_at=now,
                        )
                    )
                    # repoint the target row itself (PK includes social_account_id)
                    await db.execute(
                        PostTarget.__table__.update()
                        .where(
                            PostTarget.post_id == tgt.post_id,
                            PostTarget.social_account_id == tgt.social_account_id,
                        )
                        .values(
                            social_account_id=target_acc.id,
                            status="pending",
                            error_message=None,
                            error_class=None,
                            published_at=None,
                            platform_post_id=None,
                            platform_url=None,
                            updated_at=now,
                        )
                    )
                else:
                    # business_login already owns this post — cancel the
                    # broken lane's queue rows and mark its target skipped
                    # so nothing retries the dead account.
                    await db.execute(
                        PublishQueue.__table__.update()
                        .where(
                            PublishQueue.post_id == tgt.post_id,
                            PublishQueue.social_account_id == tgt.social_account_id,
                            PublishQueue.status.in_(
                                [QueueStatus.PENDING, QueueStatus.PROCESSING, QueueStatus.FAILED]
                            ),
                        )
                        .values(status=QueueStatus.CANCELLED)
                    )
                    tgt.status = "skipped"
                    tgt.error_message = "superseded by business_login lane"
                    tgt.updated_at = now
                repointed += 1
                logger.info(
                    "ig lane guard: repointed post %s %s -> business_login %s",
                    tgt.post_id, tgt.social_account_id, target_acc.id,
                )
        await db.commit()

    out = {"repointed": repointed, "dead_business_login": dead_tokens}
    if repointed or dead_tokens:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            msg = ":twisted_rightwards_arrows: ig-lane-guard:"
            if repointed:
                msg += f" repointed {repointed} permission-failed IG target(s) to business_login lane"
            if dead_tokens:
                msg += f" business_login token dead for {', '.join(dead_tokens)} — needs instagram2 OAuth re-drive"
            await post_alert_to_slack(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ig lane guard alert failed: %s", exc)
    return out


@shared_task(name="app.worker.tasks.instagram_lane_guard.run_instagram_lane_guard")
def run_instagram_lane_guard() -> dict:
    return run_async(_sweep())
