"""devapp-channel-actions — report → publish hop.

Reads the latest ``devapp_snapshot`` events, rebuilds the per-channel
action rows (``devapps_sync.build_channel_actions``), and hands every
healthy ``channel_action`` to ``monetization_executor.schedule_report_actions``
— the same production chain the strategy brief and monetization report use:
brand-context copy → plain-English rewrite → quality pipeline → media →
brand compliance → SCHEDULED post (or DRAFT when a gate fails).

Health overrides (dead token / quota / expiring credential) never generate
posts — the ``devapp_health`` insights action owns those alerts.

Idempotent per day: ``schedule_report_actions`` dedups on
``meta_data.devapp_channel_action`` hash = date|platform|instruction, so the
4x/day cadence only materialises the first run's worth of posts.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import text

from app.models.user import Team
from app.services.devapps_sync import (
    DEVAPP_EVENT_TYPE,
    build_channel_actions,
    monetization_actions_from_channels,
)
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_SOURCE = "devapp-channel-actions"
_DEDUP_FIELD = "devapp_channel_action"
_MAX_ACTIONS = 10  # one per connected channel, bounded


@celery_app.task(name="app.worker.tasks.devapp_channel_actions.run_channel_actions")
def run_channel_actions() -> dict:
    """Latest devapp snapshots → per-channel report posts via the executor."""
    return run_async(_run())


async def _run() -> dict:
    from app.services.monetization_executor import schedule_report_actions
    from app.services.monetization_report import MonetizationAction

    async with task_session() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT ON (social_account_id) team_id, meta_data"
                        " FROM analytics_events"
                        " WHERE event_type=:t AND social_account_id IS NOT NULL"
                        " ORDER BY social_account_id, occurred_at DESC"
                    ),
                    {"t": DEVAPP_EVENT_TYPE},
                )
            )
            .mappings()
            .all()
        )
        by_team: dict[uuid.UUID, list[dict]] = defaultdict(list)
        for r in rows:
            payload = r["meta_data"] or {}
            if isinstance(payload, dict):
                by_team[r["team_id"]].append(payload)

        report_date = datetime.now(UTC).date().isoformat()
        scheduled: list[dict] = []
        for team_id, payloads in by_team.items():
            team = await db.get(Team, team_id)
            if team is None:
                continue
            actions = monetization_actions_from_channels(build_channel_actions(payloads))
            if not actions:
                continue
            monetization_actions = [
                MonetizationAction(
                    platform=a["platform"],
                    instruction=a["instruction"],
                    format=a.get("format") or "",
                )
                for a in actions
            ]
            try:
                results = await schedule_report_actions(
                    db,
                    team,
                    monetization_actions,
                    report_date=report_date,
                    source=_SOURCE,
                    dedup_field=_DEDUP_FIELD,
                    max_actions=_MAX_ACTIONS,
                )
                scheduled.extend({"team_id": str(team_id), **r} for r in results)
            except Exception as exc:  # noqa: BLE001 — one team must not kill the sweep
                logger.warning("devapp channel actions failed for team %s: %s", team_id, str(exc)[:200])
                scheduled.append({"team_id": str(team_id), "status": "failed", "reason": str(exc)[:160]})
        await db.commit()
        return {"teams": len(by_team), "results": scheduled}
