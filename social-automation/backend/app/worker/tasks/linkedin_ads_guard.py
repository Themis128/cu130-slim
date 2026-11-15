"""LinkedIn ads spend guard — ``r_ads`` read-only lane.

Daily check: list ad accounts + their ACTIVE/PAUSED campaigns and alert
(P2) when a campaign is ACTIVE that isn't on the
``LINKEDIN_ADS_ALLOWED_ACTIVE`` allowlist (comma-separated campaign names
or ids). Rationale: cloudless.gr runs on promotional credits and any
unexpected ACTIVE campaign is either a mistake or unauthorized spend.

``r_ads_reporting`` is NOT granted, so this is detection-only — no spend
or impression data, just status inventory.
"""

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.models.analytics import AnalyticsEvent
from app.models.social_account import SocialAccount
from app.services import linkedin_ads
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

ALLOWED_ACTIVE = {
    s.strip()
    for s in os.getenv("LINKEDIN_ADS_ALLOWED_ACTIVE", "").split(",")
    if s.strip()
}


@shared_task(
    name="app.worker.tasks.linkedin_ads_guard.guard_linkedin_ads",
    bind=True,
    max_retries=0,
)
def guard_linkedin_ads(self):
    return run_async(_run())


async def _run() -> dict[str, Any]:
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
        if not acc:
            return {"skip": "no-linkedin-org-account"}
        team_id = acc.team_id

        try:
            accounts = await linkedin_ads.ad_accounts(db, team_id)
        except Exception as exc:  # noqa: BLE001 — read-only guard must not kill the beat
            logger.warning("ads-guard: adAccounts fetch failed: %s", exc)
            return {"error": str(exc)[:200]}
        if not accounts:
            return {"accounts": 0}

    stats: dict[str, Any] = {"accounts": len(accounts), "campaigns": 0, "active": 0, "unexpected_active": []}
    unexpected: list[str] = []
    for a in accounts[:5]:
        urn = a.get("urn") or f"urn:li:sponsoredAccount:{a.get('id')}"
        for cp in await linkedin_ads.campaigns(db, urn):
            stats["campaigns"] += 1
            status = (cp.get("status") or "").upper()
            if status != "ACTIVE":
                continue
            stats["active"] += 1
            name = str(cp.get("name") or "")
            cid = str(cp.get("id") or "")
            if name in ALLOWED_ACTIVE or cid in ALLOWED_ACTIVE:
                continue
            unexpected.append(f"{name or cid} ({a.get('name', urn)})")

    stats["unexpected_active"] = unexpected
    if unexpected:
        await send_telegram_alert(
            "ops",
            "LinkedIn ads guard: unexpected ACTIVE campaign(s) — "
            + "; ".join(unexpected[:10])
            + "\nAllowlist via LINKEDIN_ADS_ALLOWED_ACTIVE if intentional.",
        )
        await _persist_snapshot(team_id, stats)
    logger.info("linkedin-ads-guard: %s", stats)
    return stats


async def _persist_snapshot(team_id, stats: dict) -> None:
    """Journal the guard run so unexpected-ACTIVE events are auditable."""
    try:
        async with task_session() as db:
            acc = (
                (
                    await db.execute(
                        select(SocialAccount).where(
                            SocialAccount.platform == "linkedin",
                            SocialAccount.team_id == team_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            db.add(
                AnalyticsEvent(
                    team_id=team_id,
                    social_account_id=acc.id if acc else None,
                    event_type="linkedin_ads_guard",
                    platform="linkedin",
                    platform_event_id=f"guard-{int(time.time())}",
                    occurred_at=datetime.now(UTC),
                    meta_data=stats,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — journaling is best-effort
        logger.debug("ads-guard journal failed", exc_info=True)
